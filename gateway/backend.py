import json
import random
import re
import time
import uuid

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

import utils.globals as globals
from app import app
from chatgpt.authorization import verify_token
from chatgpt.fp import get_fp
from chatgpt.proofofWork import get_answer_token, get_config, get_requirements_token
from gateway.chatgpt import chatgpt_html
from gateway.reverseProxy import chatgpt_reverse_proxy, content_generator, get_real_req_token, headers_reject_list
from utils.Client import Client
from utils.Logger import logger
from utils.configs import x_sign, turnstile_solver_url, chatgpt_base_url_list, no_sentinel, sentinel_proxy_url_list, \
    force_no_history, redis_utils, is_true

banned_paths = [
    "backend-api/accounts/logout_all",
    "backend-api/accounts/deactivate",
    "backend-api/payments",
    "backend-api/subscriptions",
    "backend-api/user_system_messages",
    "backend-api/memories",
    "backend-api/settings/clear_account_user_memory",
    "backend-api/conversations/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    "backend-api/accounts/mfa_info",
    "backend-api/accounts/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/invites",
    "admin",
]
redirect_paths = ["auth/logout"]
chatgpt_paths = ["c/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"]


@app.get("/backend-api/accounts/check/v4-2023-04-27")
async def check_account(request: Request):
    check_account_response = await chatgpt_reverse_proxy(request, "backend-api/accounts/check/v4-2023-04-27")
    # check_account_str = check_account_response.body.decode('utf-8')
    # check_account_info = json.loads(check_account_str)
    # for key in check_account_info.get("accounts", {}).keys():
    #     account_id = check_account_info["accounts"][key]["account"]["account_id"]
    #     globals.seed_map[token]["user_id"] = \
    #         check_account_info["accounts"][key]["account"]["account_user_id"].split("__")[0]
    #     check_account_info["accounts"][key]["account"]["account_user_id"] = f"user-chatgpt__{account_id}"
    # with open(globals.SEED_MAP_FILE, "w", encoding="utf-8") as f:
    #     json.dump(globals.seed_map, f, indent=4)
    # return check_account_info
    return check_account_response

@app.post("/backend-api/gizmos/snorlax/upsert")
async def get_gizmos_upsert(request: Request):
    response = await chatgpt_reverse_proxy(request, "backend-api/gizmos/snorlax/upsert")
    if response.status_code == 200:
        gizmo_id = json.loads(response.body).get('resource').get('gizmo').get('id')
        # 获取用户
        username = redis_utils.get_username(request)
        redis_utils.set_add("user_gizmo_project:" + username, gizmo_id)
    return response

@app.get("/backend-api/gizmos/snorlax/sidebar")
async def get_gizmos_sidebar(request: Request):
    response = await chatgpt_reverse_proxy(request, "backend-api/gizmos/snorlax/sidebar")
    gizmo_projects = redis_utils.set_members("user_gizmo_project:" + redis_utils.get_username(request))
    if response.status_code == 200:
        response_json = json.loads(response.body)
        items = response_json.get('items')
        filtered_items = [item for item in items if item.get('gizmo').get('gizmo').get('id') in gizmo_projects]
        response_json['items'] = filtered_items
        return Response(
            content=json.dumps(response_json),
            media_type="application/json",
            status_code=200
        )
    return response

@app.get("/backend-api/gizmos/bootstrap")
async def get_gizmos_bootstrap(request: Request):
    # return {"gizmos": []}
    return await chatgpt_reverse_proxy(request, "backend-api/gizmos/bootstrap")


@app.get("/backend-api/gizmos/pinned")
async def get_gizmos_pinned(request: Request):
    # return {"items": [], "cursor": None}
    return await chatgpt_reverse_proxy(request, "backend-api/gizmos/pinned")


@app.get("/public-api/gizmos/discovery/recent")
async def get_gizmos_discovery_recent(request: Request):
    # return {
    #     "info": {
    #         "id": "recent",
    #         "title": "Recently Used",
    #     },
    #     "list": {
    #         "items": [],
    #         "cursor": None
    #     }
    # }
    return await chatgpt_reverse_proxy(request, "public-api/gizmos/discovery/recent")


@app.api_route("/backend-api/conversations", methods=["GET", "PATCH"])
async def get_conversations(request: Request):
    share_token = request.cookies.get("share_token", "")
    # conversation_details_response转成json
    conversation_list = await chatgpt_reverse_proxy(request, "backend-api/conversations")
    conversation_list_str = conversation_list.body.decode('utf-8')
    conversation_list_json = json.loads(conversation_list_str)

    # 获取当前用户
    username = redis_utils.get_username(request)
    conversation_isolation = redis_utils.hash_get("share_token_info:" + share_token, 'conversation_isolation')

    if conversation_isolation is not None and not is_true(conversation_isolation):
        return Response(
            content=json.dumps(conversation_list_json),
            media_type="application/json"
        )
    # 获取对话列表
    conversations = redis_utils.set_members('user_conversations:' + (share_token if username is None else username))
    if conversations is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    # 初始化map
    conversation_map = {}
    for conversation_id in conversations:
        conversation_map[conversation_id] = conversation_id

    for item in conversation_list_json['items']:
        if conversation_map.get(item['id']) is None:
            item['title'] = '🔒'

    return Response(
        content=json.dumps(conversation_list_json),
        media_type="application/json"
    )


@app.get("/backend-api/conversation/{conversation_id}")
async def update_conversation(request: Request, conversation_id: str):
    share_token = request.cookies.get("share_token", "")
    conversation_isolation = redis_utils.hash_get("share_token_info:" + share_token, 'conversation_isolation')
    if conversation_isolation is not None and is_true(conversation_isolation):
        # 获取当前用户
        username = redis_utils.get_username(request)
        # 获取对话列表
        conversations = redis_utils.set_members('user_conversations:' + username)
        if conversations is None or conversation_id not in conversations:
            raise HTTPException(status_code=403, detail="Forbidden")

    conversation_details_response = await chatgpt_reverse_proxy(request,
                                                                f"backend-api/conversation/{conversation_id}")

    return conversation_details_response


@app.patch("/backend-api/conversation/{conversation_id}")
async def patch_conversation(request: Request, conversation_id: str):
    share_token = request.cookies.get("share_token", "")

    conversation_isolation = redis_utils.hash_get("share_token_info:" + share_token, 'conversation_isolation')
    if conversation_isolation is not None and is_true(conversation_isolation):
        # 获取当前用户
        username = redis_utils.get_username(request)
        # 获取对话列表
        conversations = redis_utils.set_members('user_conversations:' + username)
        if conversations is None or conversation_id not in conversations:
            raise HTTPException(status_code=403, detail="Forbidden")

    patch_response = (await chatgpt_reverse_proxy(request, f"backend-api/conversation/{conversation_id}"))
    return patch_response


@app.get("/backend-api/me")
async def get_me(request: Request):
    share_token = request.cookies.get("share_token", "")
    token = redis_utils.hash_get('share_token_info:' + share_token, 'access_token')
    conversation_isolation = redis_utils.hash_get("share_token_info:" + share_token,
                                                  'conversation_isolation') if share_token is not None else ''

    if token.startswith("eyJhbGciOi") and conversation_isolation is not None and not is_true(conversation_isolation):
        return await chatgpt_reverse_proxy(request, "backend-api/me")
    else:
        me = {
            "object": "user",
            "id": "org-chatgpt",
            "email": "chatgpt@openai.com",
            "name": "ChatGPT",
            "picture": "https://cdn.auth0.com/avatars/ai.png",
            "created": int(time.time()),
            "phone_number": None,
            "mfa_flag_enabled": False,
            "amr": [],
            "groups": [],
            "orgs": {
                "object": "list",
                "data": [
                    {
                        "object": "organization",
                        "id": "org-chatgpt",
                        "created": 1715641300,
                        "title": "Personal",
                        "name": "user-chatgpt",
                        "description": "Personal org for chatgpt@openai.com",
                        "personal": True,
                        "settings": {
                            "threads_ui_visibility": "NONE",
                            "usage_dashboard_visibility": "ANY_ROLE",
                            "disable_user_api_keys": False
                        },
                        "parent_org_id": None,
                        "is_default": True,
                        "role": "owner",
                        "is_scale_tier_authorized_purchaser": None,
                        "is_scim_managed": False,
                        "projects": {
                            "object": "list",
                            "data": []
                        },
                        "groups": [],
                        "geography": None
                    }
                ]
            },
            "has_payg_project_spend_limit": True
        }
    return Response(content=json.dumps(me, indent=4), media_type="application/json")


@app.post("/backend-api/edge")
async def edge():
    return Response(status_code=204)


if no_sentinel:
    @app.post("/backend-api/sentinel/chat-requirements")
    async def sentinel_chat_conversations():
        return {
            "arkose": {
                "dx": None,
                "required": False
            },
            "persona": "chatgpt-paid",
            "proofofwork": {
                "difficulty": None,
                "required": False,
                "seed": None
            },
            "token": str(uuid.uuid4()),
            "turnstile": {
                "dx": None,
                "required": False
            }
        }


    @app.post("/backend-api/conversation")
    async def chat_conversations(request: Request):
        token = redis_utils.hash_get('share_token_info:' + request.cookies.get("share_token", ""), 'access_token')
        req_token = await get_real_req_token(token)
        access_token = await verify_token(req_token)
        fp = get_fp(req_token).copy()
        proxy_url = fp.pop("proxy_url", None)
        impersonate = fp.pop("impersonate", "safari15_3")
        user_agent = fp.get("user-agent",
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")

        host_url = random.choice(chatgpt_base_url_list) if chatgpt_base_url_list else "https://chatgpt.com"
        proof_token = None
        turnstile_token = None

        headers = {
            key: value for key, value in request.headers.items()
            if (key.lower() not in ["host", "origin", "referer", "priority", "sec-ch-ua-platform", "sec-ch-ua",
                                    "sec-ch-ua-mobile", "oai-device-id"] and key.lower() not in headers_reject_list)
        }
        headers.update(fp)
        headers.update({"authorization": f"Bearer {access_token}"})

        client = Client(proxy=proxy_url, impersonate=impersonate)
        if sentinel_proxy_url_list:
            clients = Client(proxy=random.choice(sentinel_proxy_url_list), impersonate=impersonate)
        else:
            clients = client

        config = get_config(user_agent)
        p = get_requirements_token(config)
        data = {'p': p}
        r = await clients.post(f'{host_url}/backend-api/sentinel/chat-requirements', headers=headers, json=data,
                               timeout=10)
        resp = r.json()
        turnstile = resp.get('turnstile', {})
        turnstile_required = turnstile.get('required')
        if turnstile_required:
            turnstile_dx = turnstile.get("dx")
            try:
                if turnstile_solver_url:
                    res = await client.post(turnstile_solver_url,
                                            json={"url": "https://chatgpt.com", "p": p, "dx": turnstile_dx})
                    turnstile_token = res.json().get("t")
            except Exception as e:
                logger.info(f"Turnstile ignored: {e}")

        proofofwork = resp.get('proofofwork', {})
        proofofwork_required = proofofwork.get('required')
        if proofofwork_required:
            proofofwork_diff = proofofwork.get("difficulty")
            proofofwork_seed = proofofwork.get("seed")
            proof_token, solved = await run_in_threadpool(
                get_answer_token, proofofwork_seed, proofofwork_diff, config
            )
            if not solved:
                raise HTTPException(status_code=403, detail="Failed to solve proof of work")
        chat_token = resp.get('token')
        headers.update({
            "openai-sentinel-chat-requirements-token": chat_token,
            "openai-sentinel-proof-token": proof_token,
            "openai-sentinel-turnstile-token": turnstile_token,
        })

        params = dict(request.query_params)
        data = await request.body()
        request_cookies = dict(request.cookies)

        async def c_close(client, clients):
            if client:
                await client.close()
                del client
            if clients:
                await clients.close()
                del clients

        history = True
        try:
            req_json = json.loads(data)
            history = not req_json.get("history_and_training_disabled", False)
        except Exception:
            pass
        if force_no_history:
            history = False
            req_json = json.loads(data)
            req_json["history_and_training_disabled"] = True
            data = json.dumps(req_json).encode("utf-8")

        background = BackgroundTask(c_close, client, clients)
        r = await client.post_stream(f"{host_url}/backend-api/conversation", params=params, headers=headers,
                                     cookies=request_cookies, data=data, stream=True, allow_redirects=False)
        rheaders = r.headers
        logger.info(f"Request token: {req_token}")
        logger.info(f"Request proxy: {proxy_url}")
        logger.info(f"Request UA: {user_agent}")
        logger.info(f"Request impersonate: {impersonate}")
        if x_sign:
            rheaders.update({"x-sign": x_sign})
        if 'stream' in rheaders.get("content-type", ""):
            conv_key = r.cookies.get("conv_key", "")
            response = StreamingResponse(content_generator(r, token, history), headers=rheaders,
                                         media_type=r.headers.get("content-type", ""), background=background)
            response.set_cookie("conv_key", value=conv_key)
            return response
        else:
            return Response(content=(await r.atext()), headers=rheaders, media_type=rheaders.get("content-type"),
                            status_code=r.status_code, background=background)

@app.get("/api/usage")
async def get_usage(share_token: str):
    """获取用户的模型使用量"""
    try:
        # 从redis获取用户名
        name_from_redis = redis_utils.hash_get('share_token_info:' + share_token, 'username')
        username = share_token if name_from_redis is None else name_from_redis
        
        # 获取该用户的所有模型使用量
        usage = redis_utils.hash_get("usage:" + username)
        
        # 如果没有使用记录则返回空字典
        if not usage:
            usage = {}
            
        return {
            "status": True,
            "message": "Success",
            "data": usage
        }
        
    except Exception as e:
        return {
            "status": False, 
            "message": str(e),
            "data": None
        }



@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE"])
async def reverse_proxy(request: Request, path: str):
    token = redis_utils.hash_get('share_token_info:' + request.cookies.get("share_token", ""), 'access_token')
    if not token.startswith("eyJhbGciOi"):
        for banned_path in banned_paths:
            if re.match(banned_path, path):
                raise HTTPException(status_code=403, detail="Forbidden")

    for chatgpt_path in chatgpt_paths:
        if re.match(chatgpt_path, path):
            return await chatgpt_html(request)

    for redirect_path in redirect_paths:
        if re.match(redirect_path, path):
            redirect_url = str(request.base_url)
            response = RedirectResponse(url=f"{redirect_url}login", status_code=302)
            return response

    return await chatgpt_reverse_proxy(request, path)
