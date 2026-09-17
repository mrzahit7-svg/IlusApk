import time as _time


def time():
    return _time.perf_counter()


def time_today():
    return _time.strftime("%H:%M:%S")


def time_stop(n):
    _time.sleep(n)
def version():
    return "1.0v"