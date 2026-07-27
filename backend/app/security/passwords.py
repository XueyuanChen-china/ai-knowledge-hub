"""密码哈希工具。

使用 Argon2id 的推荐参数。应用代码只接触 hash，绝不保存或记录明文密码。
"""

from pwdlib import PasswordHash

PASSWORD_HASHER = PasswordHash.recommended()
# 用户不存在时也执行一次 verify，降低通过响应时间枚举账号的可能性。
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("invalid-login-password")


def hash_password(password: str) -> str:
    """生成密码哈希。"""

    if not password or len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码和 Argon2 哈希是否匹配。"""

    if not password:
        return False
    return PASSWORD_HASHER.verify(password, password_hash)
