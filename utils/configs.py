"""
配置模块：负责加载和管理应用程序的所有环境变量配置
主要功能：
1. 从.env文件加载环境变量
2. 处理各种配置参数
3. 提供配置验证和转换功能
"""

import ast
import os

from dotenv import load_dotenv

from utils.Logger import logger

# 加载.env文件中的环境变量，使用ASCII编码
load_dotenv(encoding="ascii")


def is_true(x):
    """
    判断输入值是否为真值
    支持布尔值、字符串、整数等多种输入类型
    
    Args:
        x: 输入值，可以是bool、str、int等类型
    
    Returns:
        bool: 真值判断结果
    """
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ['true', '1', 't', 'y', 'yes']
    elif isinstance(x, int):
        return x == 1
    else:
        return False

# API相关配置
api_prefix = os.getenv('API_PREFIX', None)  # API前缀
authorization = os.getenv('AUTHORIZATION', '').replace(' ', '')  # 授权令牌
chatgpt_base_url = os.getenv('CHATGPT_BASE_URL', 'https://chatgpt.com').replace(' ', '')  # ChatGPT基础URL
auth_key = os.getenv('AUTH_KEY', None)  # 认证密钥
x_sign = os.getenv('X_SIGN', None)  # 签名密钥

# Arkose相关配置
ark0se_token_url = os.getenv('ARK' + 'OSE_TOKEN_URL', '').replace(' ', '')
if not ark0se_token_url:
    ark0se_token_url = os.getenv('ARK0SE_TOKEN_URL', None)

# 代理配置
proxy_url = os.getenv('PROXY_URL', '').replace(' ', '')  # 普通代理URL
sentinel_proxy_url = os.getenv('SENTINEL_PROXY_URL', None)  # Sentinel代理URL
export_proxy_url = os.getenv('EXPORT_PROXY_URL', None)  # 导出代理URL

# 资源主机配置
file_host = os.getenv('FILE_HOST', None)  # 文件服务器主机
voice_host = os.getenv('VOICE_HOST', None)  # 语音服务器主机

# 用户代理和模拟配置
impersonate_list_str = os.getenv('IMPERSONATE', '[]')  # 模拟用户列表
user_agents_list_str = os.getenv('USER_AGENTS', '[]')  # User-Agent列表
device_tuple_str = os.getenv('DEVICE_TUPLE', '()')  # 设备信息元组
browser_tuple_str = os.getenv('BROWSER_TUPLE', '()')  # 浏览器信息元组
platform_tuple_str = os.getenv('PLATFORM_TUPLE', '()')  # 平台信息元组

# Cloudflare相关配置
cf_file_url = os.getenv('CF_FILE_URL', None)  # Cloudflare文件URL
turnstile_solver_url = os.getenv('TURNSTILE_SOLVER_URL', None)  # Turnstile求解器URL

# 功能开关配置
history_disabled = is_true(os.getenv('HISTORY_DISABLED', True))  # 是否禁用历史记录
pow_difficulty = os.getenv('POW_DIFFICULTY', '000032')  # PoW难度
retry_times = int(os.getenv('RETRY_TIMES', 3))  # 重试次数
conversation_only = is_true(os.getenv('CONVERSATION_ONLY', False))  # 是否仅支持对话
enable_limit = is_true(os.getenv('ENABLE_LIMIT', True))  # 是否启用限制
upload_by_url = is_true(os.getenv('UPLOAD_BY_URL', False))  # 是否通过URL上传
check_model = is_true(os.getenv('CHECK_MODEL', False))  # 是否检查模型
scheduled_refresh = is_true(os.getenv('SCHEDULED_REFRESH', False))  # 是否启用定时刷新
random_token = is_true(os.getenv('RANDOM_TOKEN', True))  # 是否使用随机令牌
oai_language = os.getenv('OAI_LANGUAGE', 'zh-CN')  # OpenAI语言设置

# 将字符串配置转换为列表/元组
authorization_list = authorization.split(',') if authorization else []
chatgpt_base_url_list = chatgpt_base_url.split(',') if chatgpt_base_url else []
ark0se_token_url_list = ark0se_token_url.split(',') if ark0se_token_url else []
proxy_url_list = proxy_url.split(',') if proxy_url else []
sentinel_proxy_url_list = sentinel_proxy_url.split(',') if sentinel_proxy_url else []
impersonate_list = ast.literal_eval(impersonate_list_str)
user_agents_list = ast.literal_eval(user_agents_list_str)
device_tuple = ast.literal_eval(device_tuple_str)
browser_tuple = ast.literal_eval(browser_tuple_str)
platform_tuple = ast.literal_eval(platform_tuple_str)

# 网关相关配置
enable_gateway = is_true(os.getenv('ENABLE_GATEWAY', False))  # 是否启用网关
auto_seed = is_true(os.getenv('AUTO_SEED', True))  # 是否自动生成种子
force_no_history = is_true(os.getenv('FORCE_NO_HISTORY', False))  # 是否强制禁用历史记录
no_sentinel = is_true(os.getenv('NO_SENTINEL', False))  # 是否禁用Sentinel

# 读取版本信息
with open('version.txt') as f:
    version = f.read().strip()

# 输出配置信息日志
logger.info("-" * 60)
logger.info(f"Chat2Api {version} | https://github.com/lanqian528/chat2api")
logger.info("-" * 60)
logger.info("Environment variables:")
logger.info("------------------------- Security -------------------------")
logger.info("API_PREFIX:        " + str(api_prefix))
logger.info("AUTHORIZATION:     " + str(authorization_list))
logger.info("AUTH_KEY:          " + str(auth_key))
logger.info("------------------------- Request --------------------------")
logger.info("CHATGPT_BASE_URL:  " + str(chatgpt_base_url_list))
logger.info("PROXY_URL:         " + str(proxy_url_list))
logger.info("EXPORT_PROXY_URL:  " + str(export_proxy_url))
logger.info("FILE_HOST:     " + str(file_host))
logger.info("VOICE_HOST:    " + str(voice_host))
logger.info("IMPERSONATE:       " + str(impersonate_list))
logger.info("USER_AGENTS:       " + str(user_agents_list))
logger.info("---------------------- Functionality -----------------------")
logger.info("HISTORY_DISABLED:  " + str(history_disabled))
logger.info("POW_DIFFICULTY:    " + str(pow_difficulty))
logger.info("RETRY_TIMES:       " + str(retry_times))
logger.info("CONVERSATION_ONLY: " + str(conversation_only))
logger.info("ENABLE_LIMIT:      " + str(enable_limit))
logger.info("UPLOAD_BY_URL:     " + str(upload_by_url))
logger.info("CHECK_MODEL:       " + str(check_model))
logger.info("SCHEDULED_REFRESH: " + str(scheduled_refresh))
logger.info("RANDOM_TOKEN:      " + str(random_token))
logger.info("OAI_LANGUAGE:      " + str(oai_language))
logger.info("------------------------- Gateway --------------------------")
logger.info("ENABLE_GATEWAY:    " + str(enable_gateway))
logger.info("AUTO_SEED:         " + str(auto_seed))
logger.info("FORCE_NO_HISTORY: " + str(force_no_history))
logger.info("-" * 60)
