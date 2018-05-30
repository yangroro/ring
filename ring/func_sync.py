""":mod:`ring.func_sync`
is a collection of factory functions.
"""
from typing import Any, Optional, List
import time
import re
import hashlib

from . import func_base as fbase

__all__ = ('dict', 'memcache', 'redis_py', 'redis', 'disk', )


type_dict = dict


class CacheUserInterface(fbase.BaseUserInterface):

    @fbase.interface_attrs(
        transform_args=fbase.wire_kwargs_only0,
        return_annotation=lambda a: Optional[a.get('return', Any)])
    def get(self, wire, **kwargs):
        key = self.key(wire, **kwargs)
        try:
            result = self.ring.storage.get(key)
        except fbase.NotFound:
            result = self.ring.miss_value
        return result

    @fbase.interface_attrs(transform_args=fbase.wire_kwargs_only0)
    def update(self, wire, **kwargs):
        key = wire.key(**kwargs)
        result = wire.execute(**kwargs)
        self.ring.storage.set(key, result)
        return result

    @fbase.interface_attrs(transform_args=fbase.wire_kwargs_only0)
    def get_or_update(self, wire, **kwargs):
        key = self.key(wire, **kwargs)
        try:
            result = self.ring.storage.get(key)
        except fbase.NotFound:
            result = self.execute(wire, **kwargs)
            self.ring.storage.set(key, result)
        return result

    @fbase.interface_attrs(
        transform_args=fbase.wire_kwargs_only1, return_annotation=None)
    def set(self, wire, _value, **kwargs):
        key = self.key(wire, **kwargs)
        self.ring.storage.set(key, _value)

    @fbase.interface_attrs(
        transform_args=fbase.wire_kwargs_only0, return_annotation=None)
    def delete(self, wire, **kwargs):
        key = self.key(wire, **kwargs)
        self.ring.storage.delete(key)

    @fbase.interface_attrs(
        transform_args=fbase.wire_kwargs_only0, return_annotation=bool)
    def has(self, wire, **kwargs):
        key = self.key(wire, **kwargs)
        return self.ring.storage.has(key)

    @fbase.interface_attrs(
        transform_args=fbase.wire_kwargs_only0, return_annotation=None)
    def touch(self, wire, **kwargs):
        key = self.key(wire, **kwargs)
        self.ring.storage.touch(key)


def execute_bulk_item(wire, args):
    if isinstance(args, tuple):
        return wire._ring.cwrapper.callable(*(wire._preargs + args))
    elif isinstance(args, type_dict):
        return wire._ring.cwrapper.callable(*wire._preargs, **args)
    else:
        raise TypeError(
            "Each parameter of '_many' suffixed sub-functions must be an "
            "instance of 'tuple' or 'dict'")


def create_bulk_key(interface, wire, args):
    if isinstance(args, tuple):
        kwargs = wire.merge_args(args, {})
        return interface.key(wire, **kwargs)
    elif isinstance(args, type_dict):
        return interface.key(wire, **args)
    else:
        raise TypeError(
            "Each parameter of '_many' suffixed sub-functions must be an "
            "instance of 'tuple' or 'dict'")


class BulkInterfaceMixin(object):
    """Experimental."""

    @fbase.interface_attrs(
        return_annotation=lambda a: List[a.get('return', Any)])
    def execute_many(self, wire, *args_list):
        values = [execute_bulk_item(wire, args) for args in args_list]
        return values

    @fbase.interface_attrs(return_annotation=type_dict)
    def get_many(self, wire, *args_list):
        keys = [create_bulk_key(self, wire, args) for args in args_list]
        results = self.ring.storage.get_many(
            keys, miss_value=self.ring.miss_value)
        return results

    @fbase.interface_attrs(return_annotation=None)
    def update_many(self, wire, *args_list):
        keys = [create_bulk_key(self, wire, args) for args in args_list]
        values = [execute_bulk_item(wire, args) for args in args_list]
        self.ring.storage.set_many(keys, values)

    @fbase.interface_attrs(return_annotation=None)
    def delete_many(self, wire, *args_list):
        keys = [create_bulk_key(self, wire, args) for args in args_list]
        self.ring.storage.delete_many(keys)

    @fbase.interface_attrs(return_annotation=None)
    def set_many(self, wire, args_list, value_list):
        keys = [create_bulk_key(self, wire, args) for args in args_list]
        self.ring.storage.set_many(keys, value_list)

    @fbase.interface_attrs(return_annotation=None)
    def touch_many(self, wire, *args_list):
        keys = [create_bulk_key(self, wire, args) for args in args_list]
        self.ring.storage.touch_many(keys)


class BulkStorageMixin(object):

    def get_many(self, keys, miss_value):
        values = self.get_many_values(keys)
        results = [
            self.ring.coder.decode(v) if v is not fbase.NotFound else miss_value
            for v in values]
        return results

    def set_many(self, keys, values, expire=Ellipsis):
        if expire is Ellipsis:
            expire = self.ring.expire_default
        self.set_many_values(
            keys, [self.ring.coder.encode(v) for v in values], expire)

    def delete_many(self, keys):
        self.delete_many_values(keys)

    def has_many(self, keys):
        return self.has_many_values(keys)

    def touch_many(self, keys, expire=Ellipsis):
        if expire is Ellipsis:
            expire = self.ring.expire_default
        self.touch_many_values(keys, expire)


class DictStorage(fbase.CommonMixinStorage, fbase.StorageMixin):

    now = time.time

    def get_value(self, key):
        _now = self.now()
        try:
            expired_time, value = self.backend[key]
        except KeyError:
            raise fbase.NotFound
        if expired_time is not None and expired_time < _now:
            raise fbase.NotFound
        return value

    def set_value(self, key, value, expire):
        _now = self.now()
        if expire is None:
            expired_time = None
        else:
            expired_time = _now + expire
        self.backend[key] = expired_time, value

    def delete_value(self, key):
        try:
            del self.backend[key]
        except KeyError:
            pass

    def has_value(self, key):
        return key in self.backend

    def touch_value(self, key, expire):
        _now = self.now()
        try:
            expired_time, value = self.backend[key]
        except KeyError:
            return
        if expire is None:
            expired_time = None
        else:
            expired_time = _now + expire
        self.backend[key] = expired_time, value


class MemcacheStorage(
        fbase.CommonMixinStorage, fbase.StorageMixin, BulkStorageMixin):

    def get_value(self, key):
        value = self.backend.get(key)
        if value is None:
            raise fbase.NotFound
        return value

    def set_value(self, key, value, expire):
        self.backend.set(key, value, expire)

    def delete_value(self, key):
        self.backend.delete(key)

    def touch_value(self, key, expire):
        self.backend.touch(key, expire)

    def get_many_values(self, keys):
        values = self.backend.get_multi(keys)
        return [values.get(k, fbase.NotFound) for k in keys]

    def set_many_values(self, keys, values, expire):
        self.backend.set_multi({k: v for k, v in zip(keys, values)}, expire)

    def delete_many_values(self, keys):
        return self.backend.delete_multi(keys)


class RedisStorage(fbase.CommonMixinStorage, fbase.StorageMixin):
    def get_value(self, key):
        value = self.backend.get(key)
        if value is None:
            raise fbase.NotFound
        return value

    def set_value(self, key, value, expire):
        self.backend.set(key, value, expire)

    def delete_value(self, key):
        self.backend.delete(key)

    def has_value(self, key):
        return self.backend.exists(key)

    def touch_value(self, key, expire):
        if expire is None:
            raise TypeError("'touch' is requested for persistent cache")
        self.backend.expire(key, expire)

    def get_many_values(self, keys):
        values = self.backend.mget(keys)
        return [values.get(k, fbase.NotFound) for k in keys]

    def set_many_values(self, keys, values, expire):
        self.backend.mset({k: v for k, v in zip(keys, values)}, expire)


class DiskStorage(fbase.CommonMixinStorage, fbase.StorageMixin):
    def get_value(self, key):
        value = self.backend.get(key)
        if value is None:
            raise fbase.NotFound
        return value

    def set_value(self, key, value, expire):
        self.backend.set(key, value, expire)

    def delete_value(self, key):
        self.backend.delete(key)


def dict(
        obj, key_prefix=None, expire=None, coder=None, ignorable_keys=None,
        user_interface=CacheUserInterface, storage_class=DictStorage):
    """Basic Python :class:`dict` based cache.

    This backend is not designed for real products, but useful by
    keeping below in mind:

    - `functools.lrucache` is the standard library for the most of local cache.
    - Expired objects will never be removed from the dict. If the function has
      unlimited input combinations, never use dict.
    - It is designed to "simulate" cache backends, not to provide an actual
      cache backend. If a caching function is a fast job, this backend even
      can drop the performance.

    Still, it doesn't mean you can't use this backend for products. Take
    advantage of it when your demands fit.

    :param dict obj: Cache storage. Any :class:`dict` compatible object.

    :see: :func:`ring.aiodict` for :mod:`asyncio` version.
    """
    return fbase.factory(
        obj, key_prefix=key_prefix, on_manufactured=None,
        user_interface=user_interface, storage_class=storage_class,
        miss_value=None, expire_default=expire, coder=coder,
        ignorable_keys=ignorable_keys)


def memcache(
        client, key_prefix=None, expire=0, coder=None, ignorable_keys=None,
        user_interface=(CacheUserInterface, BulkInterfaceMixin),
        storage_class=MemcacheStorage):
    """Common Memcached_ interface.

    This backend is common interface for various memcached client libraries
    below:

    - https://pypi.org/project/python-memcached/
    - https://pypi.org/project/python3-memcached/
    - https://pypi.org/project/pylibmc/
    - https://pypi.org/project/pymemcache/

    Most of them expect `Memcached` client or dev package is installed on your
    machine. If you are new to Memcached, check how to install it and the
    python package on your platform.

    The expected types for input and output are always :class:`bytes` for
    `None` coder, but you may use different types depending on client
    libraries. Ring doesn't guarantee current/future behavior except for
    :class:`bytes`.

    Examples of expected client for each memcached packages:

    - pymemcache: ``pymemcache.client.Client(('127.0.0.1', 11211))``
    - python-memcached or python3-memcached:
      ``memcache.Client(["127.0.0.1:11211"])``
    - pylibmc: ``pylibmc.Client(['127.0.0.1'])``

    .. _Memcached: http://memcached.org/

    :param object client: Memcached client object. See above for details.
    :param object key_refactor: The default key refactor may hash the cache
        key when it doesn't meet memcached key restriction.

    :note: `touch` feature availability depends on memcached library.
    :see: :func:`ring.aiomcache` for :mod:`asyncio` version.
    """
    from ring._memcache import key_refactor
    miss_value = None

    return fbase.factory(
        client, key_prefix=key_prefix, on_manufactured=None,
        user_interface=user_interface, storage_class=storage_class,
        miss_value=miss_value, expire_default=expire, coder=coder,
        ignorable_keys=ignorable_keys,
        key_refactor=key_refactor)


def redis_py(
        client, key_prefix=None, expire=None, coder=None, ignorable_keys=None,
        user_interface=CacheUserInterface, storage_class=RedisStorage):
    """Redis_ interface.

    This backend depends on `redis-py`_.

    The `redis` package expects Redis client or dev package is installed on
    your machine. If you are new to Redis, check how to install Redis and the
    Python package on your platform.

    Note that :class:`redis.StrictRedis` is expected, which is different to
    :class:`redis.Redis`.

    :param redis.StrictRedis client: Redis client object.

    :see: :func:`ring.aioredis` for :mod:`asyncio` version.
    :see: Redis_ for Redis documentation.

    .. _Redis: http://redis.io/
    .. _redis-py: https://pypi.org/project/redis/
    """
    return fbase.factory(
        client, key_prefix=key_prefix, on_manufactured=None,
        user_interface=user_interface, storage_class=storage_class,
        miss_value=None, expire_default=expire, coder=coder,
        ignorable_keys=ignorable_keys)


redis = redis_py  #: Alias for redis_py for now.


def disk(
        obj, key_prefix=None, expire=None, coder=None, ignorable_keys=None,
        user_interface=CacheUserInterface, storage_class=DiskStorage):
    """diskcache_ interface.

    .. _diskcache: https://pypi.org/project/diskcache/

    :param diskcache.Cache obj: diskcache Cache object.
    """
    return fbase.factory(
        obj, key_prefix=key_prefix, on_manufactured=None,
        user_interface=user_interface, storage_class=storage_class,
        miss_value=None, expire_default=expire, coder=coder,
        ignorable_keys=ignorable_keys)


def arcus(
        client, key_prefix=None, expire=0, coder=None, ignorable_keys=None,
        user_interface=CacheUserInterface):  # pragma: no cover
    """Arcus support. deprecated."""
    class Storage(fbase.CommonMixinStorage, fbase.StorageMixin):
        def get_value(self, key):
            value = self.backend.get(key).get_result()
            if value is None:
                raise fbase.NotFound
            return value

        def set_value(self, key, value, expire):
            self.backend.set(key, value, expire)

        def delete_value(self, key):
            self.backend.delete(key)

        def touch_value(self, key, expire):
            self.backend.touch(key, expire)

    rule = re.compile(r'[!-~]+')

    def key_refactor(key):
        if len(key) < 250 and rule.match(key).group(0) == key:
            return key
        try:
            hashed = hashlib.sha1(key).hexdigest()
        except TypeError:
            # FIXME: ensure key is bytes before key_refactor
            key = key.encode('utf-8')
            hashed = hashlib.sha1(key).hexdigest()
        return 'ring-sha1:' + hashed

    return fbase.factory(
        client, key_prefix=key_prefix, on_manufactured=None,
        user_interface=user_interface, storage_class=Storage,
        miss_value=None, expire_default=expire, coder=coder,
        ignorable_keys=ignorable_keys,
        key_refactor=key_refactor)
