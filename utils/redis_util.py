import redis
from typing import Any, Optional, List, Dict, Union
import json
from fastapi import Request
import time
import logging
from redis.exceptions import ConnectionError, TimeoutError


class RedisUtils:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0,
                 password: Optional[str] = None, decode_responses: bool = True):
        """
        初始化 Redis 连接

        Args:
            host: Redis 服务器地址
            port: Redis 端口
            db: 数据库索引
            password: Redis 密码
            decode_responses: 是否自动解码响应
        """
        try:
            logger = logging.getLogger(__name__)

            # 添加重试逻辑
            max_retries = 3
            retry_delay = 1  # 秒

            for attempt in range(max_retries):
                try:
                    logger.info(f"尝试连接 Redis {host}:{port} (数据库 {db})...")
                    self.redis_client = redis.Redis(
                        host=host,
                        port=port,
                        db=db,
                        password=password,
                        decode_responses=decode_responses,
                        socket_timeout=5.0,  # 添加超时设置
                        socket_connect_timeout=5.0,
                        health_check_interval=30  # 定期检查连接健康状况
                    )
                    # 测试连接
                    self.redis_client.ping()
                    logger.info(f"Redis 连接成功 {host}:{port}")
                    break
                except (ConnectionError, TimeoutError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Redis 连接失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}，将在 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        logger.error(f"Redis 连接最终失败: {str(e)}")
                        # 仍然创建客户端，但后续操作可能会失败
                        self.redis_client = redis.Redis(
                            host=host,
                            port=port,
                            db=db,
                            password=password,
                            decode_responses=decode_responses
                        )
        except ImportError as e:
            logger.error(f"Redis 依赖导入失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Redis 初始化时发生未知错误: {str(e)}")
            raise

    # expire 方法
    def expire(self, key: str, seconds: int) -> bool:
        """
        设置键的过期时间

        Args:
            key: 键名
            seconds: 过期时间（秒）

        Returns:
            bool: 操作是否成功
        """
        try:
            return self.redis_client.expire(key, seconds)
        except Exception as e:
            print(f"Error setting expiration: {str(e)}")
            return False

    # exists 方法
    def exists(self, key: str) -> bool:
        """
        检查键是否存在

        Args:
            key: 键名

        Returns:
            bool: 键是否存在
        """
        try:
            return self.redis_client.exists(key) > 0
        except Exception as e:
            print(f"Error checking existence of key: {str(e)}")
            return False

    def set_value(self, key: str, value: Any, expire_seconds: Optional[int] = None) -> bool:
        """
        设置键值对，支持自动序列化

        Args:
            key: 键名
            value: 值（支持各种数据类型）
            expire_seconds: 过期时间（秒）

        Returns:
            bool: 操作是否成功
        """
        try:
            # 如果是复杂数据类型，转换为 JSON
            if not isinstance(value, (str, int, float, bool)):
                value = json.dumps(value)

            self.redis_client.set(key, value)
            if expire_seconds:
                self.redis_client.expire(key, expire_seconds)
            return True
        except Exception as e:
            print(f"Error setting value: {str(e)}")
            return False

    def get_value(self, key: str, default: Any = None) -> Any:
        """
        获取值，支持自动反序列化

        Args:
            key: 键名
            default: 默认值

        Returns:
            解析后的值
        """
        try:
            value = self.redis_client.get(key)
            if value is None:
                return default

            # 尝试 JSON 解析
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except Exception as e:
            print(f"Error getting value: {str(e)}")
            return default

    def delete_keys(self, *keys: str) -> int:
        """
        删除一个或多个键

        Args:
            keys: 要删除的键名

        Returns:
            int: 成功删除的键数量
        """
        try:
            return self.redis_client.delete(*keys)
        except Exception as e:
            print(f"Error deleting keys: {str(e)}")
            return 0

    def increment(self, key: str, amount: int = 1) -> Optional[int]:
        """
        递增计数器

        Args:
            key: 键名
            amount: 增加的数量

        Returns:
            Optional[int]: 增加后的值
        """
        try:
            return self.redis_client.incrby(key, amount)
        except Exception as e:
            print(f"Error incrementing value: {str(e)}")
            return None

    def hash_set(self, name: str, mapping: Dict[str, Any], expire_seconds: Optional[int] = None) -> bool:
        """
        设置哈希表的多个字段

        Args:
            name: 哈希表名
            mapping: 字段映射字典
            expire_seconds: 过期时间（秒）

        Returns:
            bool: 操作是否成功
        """
        try:
            # 将复杂数据类型转换为 JSON
            processed_mapping = {
                k: json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v
                for k, v in mapping.items()
            }
            self.redis_client.hmset(name, processed_mapping)
            if expire_seconds:
                self.redis_client.expire(name, expire_seconds)
            return True
        except Exception as e:
            print(f"Error setting hash: {str(e)}")
            return False

    def hash_get(self, name: str, key: Optional[str] = None) -> Union[Dict, Any, None]:
        """
        获取哈希表的字段值

        Args:
            name: 哈希表名
            key: 字段名（为None时获取所有字段）

        Returns:
            Union[Dict, Any, None]: 字段值或字段字典
        """
        try:
            if key is None:
                # 获取所有字段
                result = self.redis_client.hgetall(name)
            else:
                # 获取单个字段
                result = self.redis_client.hget(name, key)

            # 尝试 JSON 解析
            if isinstance(result, dict):
                return {k: self._try_json_decode(v) for k, v in result.items()}
            return self._try_json_decode(result)
        except Exception as e:
            print(f"Error getting hash: {str(e)}")
            return None

    def list_push(self, name: str, *values: Any, left: bool = True, expire_seconds: Optional[int] = None) -> Optional[
        int]:
        """
        向列表添加元素

        Args:
            name: 列表名
            values: 要添加的值
            left: 是否从左侧添加
            expire_seconds: 过期时间（秒）

        Returns:
            Optional[int]: 添加后的列表长度
        """
        try:
            # 序列化复杂数据类型
            processed_values = [
                json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v
                for v in values
            ]

            result = None
            if left:
                result = self.redis_client.lpush(name, *processed_values)
            else:
                result = self.redis_client.rpush(name, *processed_values)

            if expire_seconds:
                self.redis_client.expire(name, expire_seconds)
            return result
        except Exception as e:
            print(f"Error pushing to list: {str(e)}")
            return None

    def list_get_all(self, name: str) -> List[Any]:
        """
        获取列表所有元素

        Args:
            name: 列表名

        Returns:
            List[Any]: 列表中的所有元素
        """
        try:
            values = self.redis_client.lrange(name, 0, -1)
            return [self._try_json_decode(v) for v in values]
        except Exception as e:
            print(f"Error getting list: {str(e)}")
            return []

    def set_add(self, name: str, value: Any, expire_seconds: Optional[int] = None) -> Optional[int]:
        """
        向集合添加元素

        Args:
            name: 集合名
            values: 要添加的值
            expire_seconds: 过期时间（秒）

        Returns:
            Optional[int]: 新添加的元素数量
        """
        import logging
        logger = logging.getLogger(__name__)

        max_retries = 3
        retry_delay = 0.5  # 秒

        for attempt in range(max_retries):
            try:
                # 执行添加操作
                result = self.redis_client.sadd(name, value)

                # 设置过期时间
                if expire_seconds and result > 0:
                    expiry_result = self.redis_client.expire(name, expire_seconds)
                    if not expiry_result:
                        logger.warning(f"无法为集合 {name} 设置过期时间 {expire_seconds}秒")

                logger.info(f"向集合 {name} 添加了 {result} 个元素")
                return result

            except redis.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"集合添加操作连接错误 (尝试 {attempt + 1}/{max_retries}): {str(e)}，将在 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    logger.error(f"向集合 {name} 添加元素最终失败: {str(e)}")
                    return None
            except Exception as e:
                logger.error(f"向集合 {name} 添加元素时出错: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return None
        return None

    def set_members(self, name: str) -> List[Any]:
        """
        获取集合所有成员

        Args:
            name: 集合名

        Returns:
            List[Any]: 集合中的所有成员
        """
        import logging
        logger = logging.getLogger(__name__)

        if not name:
            logger.warning("尝试获取空名称的集合成员")
            return []

        max_retries = 3
        retry_delay = 0.5  # 秒

        for attempt in range(max_retries):
            try:
                # 检查键是否存在
                if not self.exists(name):
                    logger.info(f"集合 {name} 不存在")
                    return []

                values = self.redis_client.smembers(name)
                result = [self._try_json_decode(v) for v in values]

                logger.info(f"成功从集合 {name} 获取到 {len(result)} 个成员")
                return result

            except redis.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"获取集合成员时连接错误 (尝试 {attempt + 1}/{max_retries}): {str(e)}，将在 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    logger.error(f"从集合 {name} 获取成员最终失败: {str(e)}")
            except Exception as e:
                logger.error(f"从集合 {name} 获取成员时出错: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    break
        return []

    def _try_json_decode(self, value: Any) -> Any:
        """
        尝试 JSON 解码

        Args:
            value: 要解码的值

        Returns:
            解码后的值
        """
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def get_username(self, request: Request):
        share_token = request.cookies.get("share_token", "")
        username = self.hash_get("share_token_info:" + share_token, 'username') if share_token is not None else ''
        username = share_token if username is None or username == '' else username
        return username

    def get_username_by_token(self, share_token: str):
        username = self.hash_get("share_token_info:" + share_token, 'username') if share_token is not None else ''
        username = share_token if username is None or username == '' else username
        return username

    def close(self):
        """关闭 Redis 连接"""
        self.redis_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def set_nx(self, key: str, value: Any, expire_seconds: Optional[int] = None) -> bool:
        """
        如果键不存在则设置键值对(SET if Not eXists)

        Args:
            key: 键名
            value: 值（支持各种数据类型）
            expire_seconds: 过期时间（秒）

        Returns:
            bool: 是否设置成功
        """
        try:
            # 如果是复杂数据类型,转换为 JSON
            if not isinstance(value, (str, int, float, bool)):
                value = json.dumps(value)

            # 使用 setnx 命令设置值
            success = self.redis_client.setnx(key, value)

            # 如果设置成功且指定了过期时间
            if success and expire_seconds:
                self.redis_client.expire(key, expire_seconds)

            return success
        except Exception as e:
            print(f"Error setting nx value: {str(e)}")
            return False

    def delete(self, key: str) -> bool:
        """
        删除指定的键

        Args:
            key: 要删除的键名

        Returns:
            bool: 操作是否成功
        """
        try:
            return bool(self.redis_client.delete(key))
        except Exception as e:
            print(f"Error deleting key: {str(e)}")
            return False
