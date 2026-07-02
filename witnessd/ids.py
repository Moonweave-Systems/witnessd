import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def new_run_id() -> str:
    ms = int(time.time() * 1000)
    rand = secrets.randbits(80)
    return _encode(ms, 10) + _encode(rand, 16)


def _self_test() -> None:
    assert len(new_run_id()) == 26
