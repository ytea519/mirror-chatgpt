import hashlib

from entity.share import Share


def access_to_share(share_info: Share):
    share_token = generate_short_token(share_info.access_token, share_info.user_name)
    return share_token


def generate_short_token(access_token, username):
    # 将用户名和长token拼接
    combined_string = access_token + username
    # 使用SHA-256哈希算法生成固定长度的短token
    short_token = "fk-" + hashlib.sha256(combined_string.encode()).hexdigest()[:16]  # 取前16位作为短token
    return short_token
