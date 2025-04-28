import logging

from utils.configs import redis_utils

def get_access_token(share_token):
    return redis_utils.hash_get('share_token_info:' + share_token, 'access_token')

def get_share_token(request):
    return request.cookies.get("share_token")

def get_user_proxy(share_token):
    if not share_token:
        return None
    proxy = redis_utils.hash_get('share_token_info:' + share_token, 'proxy_url')
    return proxy