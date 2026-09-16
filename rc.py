import gc
def collect():
    return gc.collect()
def count():
    return gc.get_count()
def get_threshold():
    return gc.get_threshold()
