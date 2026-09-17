import base64 as _base64


def encode(text):
    return _base64.b64encode(text.encode()).decode()


def decode(text):
    return _base64.b64decode(text).decode()


def version():
    return "1.0v"