import pickle


def dump(data, path):
    with open(path, 'wb') as fw:
        pickle.dump(data, fw)


def load(path):
    with open(path, 'rb') as fr:
        return pickle.load(fr)


def dumps(obj):
    return pickle.dumps(obj)


def loads(obj):
    return pickle.loads(obj)
