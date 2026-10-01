"""One provider budget across API and all worker processes."""
import time
import uuid
from functools import lru_cache
from backend.config import get_settings

@lru_cache(maxsize=1)
def client():
    from redis import Redis
    return Redis.from_url(get_settings().redis_url,socket_connect_timeout=5,socket_timeout=5)

SCRIPT='''
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) + tonumber(stamp[2])/1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-60)
if redis.call('ZCARD',KEYS[1]) < tonumber(ARGV[1]) then
 redis.call('ZADD',KEYS[1],now,ARGV[2])
 redis.call('EXPIRE',KEYS[1],120)
 return 0
end
local oldest=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES')
return math.ceil((tonumber(oldest[2])+60-now)*1000)
'''

def acquire(cap):
    from backend.services.cancellation import check_cancelled
    while True:
        check_cancelled()
        wait=client().eval(SCRIPT,1,'aimarker:gemini:rpm',max(1,int(cap)),uuid.uuid4().hex)
        if wait<=0:return
        time.sleep(min(wait/1000,.5))
