import hashlib
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone, timedelta
import asyncio

from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from starlette.background import BackgroundTask

import utils.globals as globals
from chatgpt.authorization import get_req_token
from chatgpt.fp import get_fp
from gateway import common_utils
from utils.Client import Client
from utils.Logger import logger
from utils.configs import chatgpt_base_url_list, sentinel_proxy_url_list, force_no_history, file_host, voice_host, \
    redis_utils, is_true


def generate_current_time():
    current_time = datetime.now(timezone.utc)
    formatted_time = current_time.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    return formatted_time


headers_reject_list = [
    "x-real-ip",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-host",
    "x-forwarded-server",
    "cf-warp-tag-id",
    "cf-visitor",
    "cf-ray",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cdn-loop",
    "remote-host",
    "x-frame-options",
    "x-xss-protection",
    "x-content-type-options",
    "content-security-policy",
    "host",
    "cookie",
    "connection",
    "content-length",
    "content-encoding",
    "x-middleware-prefetch",
    "x-nextjs-data",
    "purpose",
    "x-forwarded-uri",
    "x-forwarded-path",
    "x-forwarded-method",
    "x-forwarded-protocol",
    "x-forwarded-scheme",
    "cf-request-id",
    "cf-worker",
    "cf-access-client-id",
    "cf-access-client-device-type",
    "cf-access-client-device-model",
    "cf-access-client-device-name",
    "cf-access-client-device-brand",
    "x-middleware-prefetch",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-server",
    "x-real-ip",
    "x-forwarded-port",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cf-ray",
    "cf-visitor",
]

headers_accept_list = [
    "openai-sentinel-chat-requirements-token",
    "openai-sentinel-proof-token",
    "openai-sentinel-turnstile-token",
    "accept",
    "authorization",
    "accept-encoding",
    "accept-language",
    "content-type",
    "oai-device-id",
    "oai-echo-logs",
    "oai-language",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
]


async def get_real_req_token(token):
    req_token = get_req_token(token)
    if len(req_token) == 45 or req_token.startswith("eyJhbGciOi"):
        return req_token
    else:
        req_token = get_req_token("", token)
        return req_token


def save_conversation(token, conversation_id, title=None):
    if conversation_id not in globals.conversation_map:
        conversation_detail = {
            "id": conversation_id,
            "title": title,
            "create_time": generate_current_time(),
            "update_time": generate_current_time()
        }
        globals.conversation_map[conversation_id] = conversation_detail
    else:
        globals.conversation_map[conversation_id]["update_time"] = generate_current_time()
        if title:
            globals.conversation_map[conversation_id]["title"] = title
    if conversation_id not in globals.seed_map[token]["conversations"]:
        globals.seed_map[token]["conversations"].insert(0, conversation_id)
    else:
        globals.seed_map[token]["conversations"].remove(conversation_id)
        globals.seed_map[token]["conversations"].insert(0, conversation_id)
    with open(globals.CONVERSATION_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(globals.conversation_map, f, indent=4)
    with open(globals.SEED_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(globals.seed_map, f, indent=4)
    if title:
        logger.info(f"Conversation ID: {conversation_id}, Title: {title}")


async def content_generator(r, share_token, history=True, request: Request = None):
    conversation_id = None
    model = None

    gpt_reset_every_day = redis_utils.hash_get('share_token_info:' + share_token, 'gpt_reset_every_day')
    username = redis_utils.get_username_by_token(share_token)
    logger.info(f"开始处理用户 {username} 的对话流")

    async for chunk in r.aiter_content():
        try:
            if history and (not conversation_id or not model):
                chat_chunk = chunk.decode('utf-8', errors='ignore')
                if chat_chunk.startswith("data: {"):
                    if "\n\nevent: delta" in chat_chunk:
                        index = chat_chunk.find("\n\nevent: delta")
                        chunk_data = chat_chunk[6:index]
                    elif "\n\ndata: {" in chat_chunk:
                        index = chat_chunk.find("\n\ndata: {")
                        chunk_data = chat_chunk[6:index]
                    else:
                        chunk_data = chat_chunk[6:]
                    chunk_data = chunk_data.strip()
                    if conversation_id is None:
                        try:
                            chunk_json = json.loads(chunk_data)
                            conversation_id = chunk_json.get("conversation_id")
                            if conversation_id is not None:
                                logger.info(f"正在保存 conversation_id: {conversation_id} 到 Redis")
                                # 添加操作结果检查
                                result = redis_utils.set_add('user_conversations:' + username, conversation_id)
                                if result:
                                    logger.info(f"成功保存 conversation_id: {conversation_id} 到 Redis")
                                else:
                                    logger.error(f"保存 conversation_id: {conversation_id} 到 Redis 失败")
                            else:
                                logger.warning("从响应数据中未获取到 conversation_id")
                        except json.JSONDecodeError as je:
                            logger.error(f"解析JSON数据失败: {je}, 原始数据: {chunk_data[:100]}...")
                        except Exception as e:
                            logger.error(f"处理 conversation_id 时出错: {str(e)}")
                    if model is None:
                        try:
                            chunk_json = json.loads(chunk_data)
                            message_data = chunk_json.get("message", {})
                            if not isinstance(message_data, dict):
                                message_data = {}
                            metadata = message_data.get("metadata", {})
                            if not isinstance(metadata, dict):
                                metadata = {}
                            model = metadata.get("model_slug")
                            if model is not None:
                                logger.info(f"获取到用户 {username} 使用的模型: {model}")
                                model_usage = redis_utils.hash_get("usage:" + username, model)
                                real_usage = 0 if model_usage is None else int(model_usage)
                                if is_true(gpt_reset_every_day):
                                    redis_result = redis_utils.hash_set("usage:" + username, {model: real_usage + 1})
                                    if not redis_result:
                                        logger.error(f"保存模型使用量失败: {model}")
                                else:
                                    # 计算到第二天0点的秒数
                                    now = datetime.now()
                                    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                                    expire_seconds = int((tomorrow - now).total_seconds())
                                    redis_result = redis_utils.hash_set("usage:" + username, {model: real_usage + 1},
                                                        expire_seconds=expire_seconds)
                                    if not redis_result:
                                        logger.error(f"保存模型使用量(带过期时间)失败: {model}")
                            else:
                                logger.warning("从响应数据中未获取到模型信息")
                        except json.JSONDecodeError as je:
                            logger.error(f"解析JSON数据失败: {je}, 原始数据: {chunk_data[:100]}...")
                        except Exception as e:
                            logger.error(f"处理模型信息时出错: {str(e)}")

        except Exception as e:
            logger.error(f"处理响应块时发生异常: {str(e)}")
        yield chunk


def get_proxy(share_token: str, fp: dict):
    proxy = common_utils.get_user_proxy(share_token)
    proxy if proxy is not None else fp.pop("proxy_url", None)
    proxy_url = proxy if proxy is not None else os.getenv('PROXY_URL', '')
    logger.info("用户代理地址："+ proxy_url)
    return proxy_url


async def content_generator_with_lock_release(r, share_token, history, lock_key=None):
    conversation_id = None
    model = None
    chunk_count = 0
    chunk_sent_count = 0  # 新增：记录发送到前端的数据块数量
    finish_marker_received = False
    start_time = time.time()
    last_chunk_time = time.time()
    username = redis_utils.get_username_by_token(share_token)
    gpt_reset_every_day = redis_utils.hash_get('share_token_info:' + share_token, 'gpt_reset_every_day')

    # 创建后台任务定期更新锁
    async def extend_lock_lifetime():
        try:
            while True:
                # 每15秒续期一次锁
                if lock_key:
                    try:
                        lock_exists = redis_utils.exists(lock_key)
                        if lock_exists:
                            redis_utils.expire(lock_key, 60)
                            logger.info(f"后台任务更新锁过期时间: {lock_key}")
                    except Exception as e:
                        logger.error(f"后台任务更新锁失败: {str(e)}")
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            logger.info(f"锁续期任务被取消: {lock_key}")
            return

    # 启动后台任务
    if lock_key:
        lock_extend_task = asyncio.create_task(extend_lock_lifetime())

    try:
        logger.info(f"开始流式处理，lock_key: {lock_key}, 开始时间: {datetime.now().isoformat()}")

        async for chunk in r.aiter_content():
            current_time = time.time()
            chunk_count += 1
            time_since_last = current_time - last_chunk_time

            # 记录每个数据块的接收时间和间隔
            chunk_size = len(chunk)
            # logger.info(f"接收到第 {chunk_count} 个数据块, 大小: {chunk_size}字节, 间隔: {time_since_last:.3f}秒, 时间: {datetime.now().isoformat()}")
            last_chunk_time = current_time

            # 检查是否为流结束标记
            try:
                chunk_text = chunk.decode('utf-8', errors='ignore').strip()
                if chunk_text == "data: [DONE]":
                    finish_marker_received = True
                    logger.info(f"收到流结束标记，总数据块: {chunk_count}, 已发送: {chunk_sent_count}, 总时间: {current_time - start_time:.3f}秒")
                    # 收到结束标记立即释放锁
                    if lock_key:
                        redis_utils.delete(lock_key)
                        logger.info(f"收到结束标记，立即释放锁: {lock_key}")
                        lock_key = None  # 防止finally中重复释放
            except Exception as e:
                logger.error(f"解析流结束标记出错: {str(e)}")

            # 更智能的超时检测 - 不使用固定90秒，而是检测数据活动
            if time_since_last > 20 and current_time - start_time > 30 and lock_key:
                logger.warning(f"数据流20秒无活动，释放锁: {lock_key}, 总时间: {current_time - start_time:.3f}秒")
                try:
                    redis_utils.delete(lock_key)
                    lock_key = None  # 防止finally中重复释放
                except Exception as e:
                    logger.error(f"释放锁失败: {str(e)}")

            # 发送一个保活信号，防止连接超时
            if time_since_last > 10 and not finish_marker_received:
                logger.info("发送保活信号")
                # 降低保活信号间隔到10秒
                yield b":\n\n"  # SSE注释作为保活信号
                chunk_sent_count += 1

            # 正常处理数据...
            try:
                if history and (not conversation_id or not model):
                    chat_chunk = chunk.decode('utf-8', errors='ignore')
                    if chat_chunk.startswith("data: {"):
                        if "\n\nevent: delta" in chat_chunk:
                            index = chat_chunk.find("\n\nevent: delta")
                            chunk_data = chat_chunk[6:index]
                        elif "\n\ndata: {" in chat_chunk:
                            index = chat_chunk.find("\n\ndata: {")
                            chunk_data = chat_chunk[6:index]
                        else:
                            chunk_data = chat_chunk[6:]
                        chunk_data = chunk_data.strip()
                        if conversation_id is None:
                            try:
                                conversation_id = json.loads(chunk_data).get("conversation_id")
                                if conversation_id is not None:
                                    result = redis_utils.set_add('user_conversations:' + username, conversation_id)
                                    if result:
                                        logger.info(f"获取到并成功保存 conversation_id: {conversation_id}")
                                    else:
                                        logger.error(f"获取到 conversation_id: {conversation_id}，但保存到 Redis 失败")
                                else:
                                    logger.warning("从响应数据中未获取到 conversation_id")
                            except json.JSONDecodeError as je:
                                logger.error(f"解析JSON数据失败: {je}, 原始数据: {chunk_data[:100]}...")
                            except Exception as e:
                                logger.error(f"处理 conversation_id 时出错: {str(e)}")
                        if model is None:
                            try:
                                chunk_json = json.loads(chunk_data)
                                message_data = chunk_json.get("message", {})
                                if not isinstance(message_data, dict):
                                    message_data = {}
                                metadata = message_data.get("metadata", {})
                                if not isinstance(metadata, dict):
                                    metadata = {}
                                model = metadata.get("model_slug")
                                if model is not None:
                                    logger.info(f"获取到模型: {model}")
                                    model_usage = redis_utils.hash_get("usage:" + username, model)
                                    real_usage = 0 if model_usage is None else int(model_usage)
                                    
                                    if is_true(gpt_reset_every_day):
                                        redis_result = redis_utils.hash_set("usage:" + username, {model: real_usage + 1})
                                        if not redis_result:
                                            logger.error(f"保存模型使用量失败: {model}")
                                    else:
                                        # 计算到第二天0点的秒数
                                        now = datetime.now()
                                        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                                     microsecond=0)
                                        expire_seconds = int((tomorrow - now).total_seconds())
                                        redis_result = redis_utils.hash_set("usage:" + username, {model: real_usage + 1},
                                                            expire_seconds=expire_seconds)
                                        if not redis_result:
                                            logger.error(f"保存模型使用量(带过期时间)失败: {model}")
                                else:
                                    logger.warning("从响应数据中未获取到模型信息")
                            except json.JSONDecodeError as je:
                                logger.error(f"解析JSON数据失败: {je}, 原始数据: {chunk_data[:100]}...")
                            except Exception as e:
                                logger.error(f"处理模型信息时出错: {str(e)}")
            except Exception as e:
                logger.error(f"处理数据块出错: {str(e)}")

            # 发送数据块给客户端
            try:
                logger.info(f"正在发送第 {chunk_count} 个数据块到前端")
                yield chunk
                chunk_sent_count += 1
                logger.info(f"成功发送第 {chunk_count} 个数据块到前端")
            except Exception as e:
                logger.error(f"发送数据块到前端失败: {str(e)}")
                # 如果发送失败，尝试释放锁
                if lock_key:
                    try:
                        redis_utils.delete(lock_key)
                        logger.info(f"发送失败，释放锁: {lock_key}")
                        lock_key = None
                    except:
                        pass
                raise  # 重新抛出异常让外层处理

    except Exception as e:
        logger.error(f"流式处理发生异常: {str(e)}")
        if not finish_marker_received:
            logger.info("发送强制结束标记")
            try:
                yield b"data: [DONE]\n\n"
                chunk_sent_count += 1
            except Exception as ex:
                logger.error(f"发送结束标记失败: {str(ex)}")
        raise
    finally:
        # 取消锁续期任务
        if 'lock_extend_task' in locals():
            lock_extend_task.cancel()

        # 确保锁被释放
        if lock_key:
            try:
                redis_utils.delete(lock_key)
                logger.info(f"finally块中释放锁: {lock_key}, 总时间: {time.time() - start_time:.3f}秒, 收到: {chunk_count}, 发送: {chunk_sent_count}")
            except Exception as e:
                logger.error(f"释放锁失败: {str(e)}")


async def chatgpt_reverse_proxy(request: Request, path: str):
    # if ".js.map" in path:
    #     raise HTTPException(status_code=405, detail="Not Al")

    try:
        share_token = common_utils.get_share_token(request)
        origin_host = request.url.netloc
        if request.url.is_secure:
            petrol = "https"
        else:
            petrol = "http"
        if "x-forwarded-proto" in request.headers:
            petrol = request.headers["x-forwarded-proto"]
        if "cf-visitor" in request.headers:
            cf_visitor = json.loads(request.headers["cf-visitor"])
            petrol = cf_visitor.get("scheme", petrol)

        params = dict(request.query_params)
        request_cookies = dict(request.cookies)

        # headers = {
        #     key: value for key, value in request.headers.items()
        #     if (key.lower() not in ["host", "origin", "referer", "priority",
        #                             "oai-device-id"] and key.lower() not in headers_reject_list)
        # }
        headers = {
            key: value for key, value in request.headers.items()
            if (key.lower() in headers_accept_list)
        }

        base_url = random.choice(chatgpt_base_url_list) if chatgpt_base_url_list else "https://chatgpt.com"
        if "assets/" in path:
            base_url = "https://cdn.oaistatic.com"
        if "file-" in path and "backend-api" not in path:
            base_url = "https://files.oaiusercontent.com"
        if "v1/" in path:
            base_url = "https://ab.chatgpt.com"
        if "sandbox" in path:
            base_url = "https://web-sandbox.oaiusercontent.com"
            path = path.replace("sandbox/", "")
        access_token = request.headers.get('authorization') \
            if 'authorization' in request.headers and request.headers.get("authorization") != "Bearer" \
            else common_utils.get_access_token(share_token)
        access_token = access_token.replace("Bearer ", "")
        headers.update({"authorization": f"Bearer {access_token}"})

        fp = get_fp(access_token).copy()
        session_id = hashlib.md5(access_token.encode()).hexdigest()

        proxy_url = get_proxy(share_token, fp)
        fp.pop("proxy_url", None)
        impersonate = fp.pop("impersonate", "safari15_3")
        user_agent = fp.get("user-agent")
        headers.update(fp)

        headers.update({
            "accept-language": "en-US,en;q=0.9",
            "host": base_url.replace("https://", "").replace("http://", ""),
            "origin": base_url,
            "referer": f"{base_url}/"
        })
        if "v1/initialize" in path:
            headers.update({"user-agent": request.headers.get("user-agent")})
            if "statsig-api-key" not in headers:
                headers.update({
                    "statsig-sdk-type": "js-client",
                    "statsig-api-key": "client-tnE5GCU2F2cTxRiMbvTczMDT1jpwIigZHsZSdqiy4u",
                    "statsig-sdk-version": "5.1.0",
                    "statsig-client-time": int(time.time() * 1000),
                })

        data = await request.body()

        history = True
        if path.endswith("backend-api/conversation") or path.endswith("backend-alt/conversation"):
            try:
                # 获取用户名作为锁的唯一标识
                access_token = common_utils.get_access_token(share_token)
                lock_key = f"chat_lock:{access_token}"

                # 尝试获取锁，设置过期时间为30秒，防止死锁
                lock_acquired = redis_utils.set_nx(lock_key, "1", expire_seconds=60)

                # 如果获取不到锁，说明有另一个对话请求正在处理中
                if not lock_acquired:
                    req_json = json.loads(data)

                    async def concurrency_limit_message_generator():
                        current_time = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace(
                            '+00:00', 'Z')
                        conversation_id = req_json.get('conversation_id', str(uuid.uuid4()))
                        parent_id = req_json.get('messages')[0].get('id')
                        message_id = req_json.get('message_id', str(uuid.uuid4()))

                        # 返回并发限制的消息
                        first_message = {
                            "conversation_id": conversation_id,
                            "message": {
                                "id": message_id,
                                "author": {"role": "assistant"},
                                "create_time": current_time,
                                "content": {
                                    "content_type": "text",
                                    "parts": ["正在排队中...请稍等(60s)并尝试发起新的提问"]
                                },
                                "metadata": {
                                    "citations": [],
                                    "content_references": [],
                                    "message_type": "next",
                                    "metadata": {"model_slug": "gpt-4o"},
                                    "default_model_slug": "gpt-4o",
                                    "parent_id": parent_id,
                                    "model_switcher_deny": []
                                },
                                "channel": None,
                                "status": "finished_successfully",
                                "end_turn": None,
                                "weight": 1.0,
                                "recipient": "all"
                            },
                            "parent_message_id": message_id
                        }
                        yield f"data: {json.dumps(first_message)}\n\n"

                        final_message = {
                            "conversation_id": conversation_id,
                            "message": {
                                "id": message_id,
                                "author": {"role": "assistant"},
                                "create_time": current_time,
                                "content": {
                                    "content_type": "text",
                                    "parts": ["正在排队中...请稍等(60s)并尝试发起新的提问"]
                                },
                                "metadata": {
                                    "citations": [],
                                    "content_references": [],
                                    "message_type": "next",
                                    "metadata": {"model_slug": "gpt-4o"},
                                    "default_model_slug": "gpt-4o",
                                    "parent_id": parent_id,
                                    "model_switcher_deny": []
                                },
                                "channel": None,
                                "status": "finished_successfully",
                                "end_turn": None,
                                "weight": 1.0,
                                "recipient": "all"
                            },
                            "parent_message_id": message_id
                        }
                        yield f"data: {json.dumps(final_message)}\n\n"
                        yield "data: [DONE]\n\n"

                    return StreamingResponse(
                        content=concurrency_limit_message_generator(),
                        media_type="text/event-stream",
                        headers={"Content-Type": "text/event-stream"}
                    )

                # 处理请求继续...
                req_json = json.loads(data)
                model = req_json.get("model")
                if model:
                    # 从redis获取用户名和token信息
                    share_token = request.cookies.get("share_token", "")
                    username = redis_utils.get_username(request)

                    # 获取该用户的使用量和限额
                    usage = redis_utils.hash_get("usage:" + username) or {}
                    current_usage = int(usage.get(model, 0))

                    # 获取用户限额信息
                    share_info = redis_utils.hash_get('share_token_info:' + share_token)

                    # 根据不同模型判断限额
                    limit = int(share_info.get(model.replace("-", "_") + '_limit', -1))

                    # 检查是否达到限额
                    if limit != -1 and current_usage >= limit:
                        # 释放锁，因为请求被限制
                        redis_utils.delete(lock_key)

                        async def limit_message_generator():
                            current_time = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace(
                                '+00:00', 'Z')
                            conversation_id = req_json.get('conversation_id', str(uuid.uuid4()))
                            parent_id = req_json.get('messages')[0].get('id')
                            message_id = req_json.get('message_id', str(uuid.uuid4()))

                            # 第一条消息，包含conversation_id等基本信息
                            first_message = {
                                "conversation_id": conversation_id,
                                "message": {
                                    "id": message_id,
                                    "author": {"role": "assistant"},
                                    "create_time": current_time,
                                    "content": {
                                        "content_type": "text",
                                        "parts": ["正在排队中...请稍等(60s)并尝试发起新的提问"]
                                    },
                                    "metadata": {
                                        "citations": [],
                                        "content_references": [],
                                        "message_type": "next",
                                        "metadata": {"model_slug": "gpt-4o"},
                                        "default_model_slug": "gpt-4o",
                                        "parent_id": parent_id,
                                        "model_switcher_deny": []
                                    },
                                    "channel": None,
                                    "status": "finished_successfully",
                                    "end_turn": None,
                                    "weight": 1.0,
                                    "recipient": "all"
                                },
                                "parent_message_id": message_id
                            }
                            yield f"data: {json.dumps(first_message)}\n\n"

                            # 第二条消息，表示会话结束
                            final_message = {
                                "conversation_id": conversation_id,
                                "message": {
                                    "id": message_id,
                                    "author": {"role": "assistant"},
                                    "create_time": current_time,
                                    "content": {
                                        "content_type": "text",
                                        "parts": ["正在排队中...请稍等(60s)并尝试发起新的提问"]
                                    },
                                    "metadata": {
                                        "citations": [],
                                        "content_references": [],
                                        "message_type": "next",
                                        "metadata": {"model_slug": "gpt-4o"},
                                        "default_model_slug": "gpt-4o",
                                        "parent_id": parent_id,
                                        "model_switcher_deny": []
                                    },
                                    "channel": None,
                                    "status": "finished_successfully",
                                    "end_turn": None,
                                    "weight": 1.0,
                                    "recipient": "all"
                                },
                                "parent_message_id": message_id
                            }
                            yield f"data: {json.dumps(final_message)}\n\n"
                            yield "data: [DONE]\n\n"

                        return StreamingResponse(
                            content=limit_message_generator(),
                            media_type="text/event-stream",
                            headers={"Content-Type": "text/event-stream"}
                        )

                        logger.info(f"用户 {username} 使用 {model} 模型,当前使用量: {current_usage}/{limit}")
            except Exception as e:
                # 确保发生异常时释放锁
                try:
                    access_token = common_utils.get_access_token(share_token)
                    lock_key = f"chat_lock:{access_token}"
                    redis_utils.delete(lock_key)
                except:
                    pass
                logger.error(f"处理对话请求时出错: {str(e)}")
                if isinstance(e, HTTPException):
                    raise e

            try:
                history = not req_json.get("history_and_training_disabled", False)
            except Exception:
                pass
            if force_no_history:
                history = False
                req_json = json.loads(data)
                req_json["history_and_training_disabled"] = True
                data = json.dumps(req_json).encode("utf-8")

        if "backend-api/sentinel/chat-requirements" in path and sentinel_proxy_url_list:
            sentinel_proxy_url = random.choice(sentinel_proxy_url_list).replace("{}",
                                                                                session_id) if sentinel_proxy_url_list else None
            client = Client(proxy=sentinel_proxy_url)
        else:
            proxy_url = proxy_url.replace("{}", session_id) if proxy_url else None
            client = Client(proxy=proxy_url, impersonate=impersonate)
        try:
            background = BackgroundTask(client.close)
            # 记录是否需要在请求完成后释放锁
            need_release_lock = False
            lock_key = ""

            # 获取锁信息以便后续释放
            if path.endswith("backend-api/conversation") or path.endswith("backend-alt/conversation"):
                access_token = common_utils.get_access_token(share_token)
                lock_key = f"chat_lock:{access_token}"
                need_release_lock = True

            if not path.endswith(".js"):
                r = await client.request(request.method, f"{base_url}/{path}", params=params, headers=headers,
                                         cookies=request_cookies, data=data, stream=True, allow_redirects=False)
            else:
                r = await client.request(request.method, f"{base_url}/{path}", params=params, data=data, stream=True,
                                         allow_redirects=False)
            if r.status_code == 307 or r.status_code == 302 or r.status_code == 301:
                # 处理重定向情况时释放锁
                if need_release_lock:
                    redis_utils.delete(lock_key)
                return Response(status_code=307,
                                headers={"Location": r.headers.get("Location")
                                .replace("ab.chatgpt.com", origin_host)
                                .replace("chatgpt.com", origin_host)
                                .replace("cdn.oaistatic.com", origin_host)
                                .replace("https", petrol)}, background=background)
            elif 'stream' in r.headers.get("content-type", ""):
                logger.info(f"Request token: {access_token}")
                logger.info(f"Request proxy: {proxy_url}")
                logger.info(f"Request UA: {user_agent}")
                logger.info(f"Request impersonate: {impersonate}")

                # 创建自定义的内容生成器，以便在流结束时释放锁
                if need_release_lock:
                    generator = content_generator_with_lock_release(r, share_token, history, lock_key)
                else:
                    generator = content_generator(r, share_token, history)

                response = StreamingResponse(
                    content=generator,
                    media_type="text/event-stream",
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",  # 重要：禁用 Nginx 缓冲
                        "Transfer-Encoding": "chunked"  # 显式使用分块传输
                    }
                )
                conv_key = r.cookies.get("conv_key", "")
                if conv_key:
                    response.set_cookie("conv_key", value=conv_key)
                return response
            elif 'image' in r.headers.get("content-type", "") or "audio" in r.headers.get("content-type",
                                                                                          "") or "video" in r.headers.get(
                "content-type", ""):
                # 处理媒体内容时释放锁
                if need_release_lock:
                    redis_utils.delete(lock_key)
                rheaders = dict(r.headers)
                response = Response(content=await r.acontent(), headers=rheaders,
                                    status_code=r.status_code, background=background)
                return response
            else:
                # 处理其他响应时释放锁
                if need_release_lock:
                    redis_utils.delete(lock_key)
                if path.endswith("backend-api/conversation") or path.endswith(
                        "backend-alt/conversation") or "/register-websocket" in path:
                    response = Response(content=(await r.acontent()), media_type=r.headers.get("content-type"),
                                        status_code=r.status_code, background=background)
                else:
                    content = await r.atext()
                    if "public-api/" in path:
                        content = (content
                                   .replace("https://ab.chatgpt.com", f"{petrol}://{origin_host}")
                                   .replace("https://cdn.oaistatic.com", f"{petrol}://{origin_host}")
                                   .replace("webrtc.chatgpt.com", voice_host if voice_host else "webrtc.chatgpt.com")
                                   .replace("files.oaiusercontent.com",
                                            file_host if file_host else "files.oaiusercontent.com")
                                   .replace("chatgpt.com/ces", f"{origin_host}/ces")
                                   )
                    else:
                        content = (content
                                   .replace("https://ab.chatgpt.com", f"{petrol}://{origin_host}")
                                   .replace("https://cdn.oaistatic.com", f"{petrol}://{origin_host}")
                                   .replace("webrtc.chatgpt.com", voice_host if voice_host else "webrtc.chatgpt.com")
                                   .replace("files.oaiusercontent.com",
                                            file_host if file_host else "files.oaiusercontent.com")
                                   .replace("web-sandbox.oaiusercontent.com", f"{origin_host}/sandbox")
                                   .replace("https://chatgpt.com", f"{petrol}://{origin_host}")
                                   .replace("chatgpt.com/ces", f"{origin_host}/ces")
                                   )
                    if base_url == "https://web-sandbox.oaiusercontent.com":
                        content = content.replace("/assets", "/sandbox/assets")
                    rheaders = dict(r.headers)
                    content_type = rheaders.get("content-type", "")
                    cache_control = rheaders.get("cache-control", "")
                    expires = rheaders.get("expires", "")
                    content_disposition = rheaders.get("content-disposition", "")
                    rheaders = {
                        "cache-control": cache_control,
                        "content-type": content_type,
                        "expires": expires,
                        "content-disposition": content_disposition
                    }
                    response = Response(content=content, headers=rheaders,
                                        status_code=r.status_code, background=background)
                return response
        except Exception as e:
            await client.close()
            # 确保在整个请求出错时也释放锁
            if path.endswith("backend-api/conversation") or path.endswith("backend-alt/conversation"):
                try:
                    access_token = common_utils.get_access_token(share_token)
                    lock_key = f"chat_lock:{access_token}"
                    redis_utils.delete(lock_key)
                except:
                    pass
    except HTTPException as e:
        raise e
    except Exception as e:
        # 在最外层异常中也尝试释放锁
        try:
            if path and (path.endswith("backend-api/conversation") or path.endswith("backend-alt/conversation")):
                access_token = common_utils.get_access_token(share_token)
                lock_key = f"chat_lock:{access_token}"
                redis_utils.delete(lock_key)
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))
