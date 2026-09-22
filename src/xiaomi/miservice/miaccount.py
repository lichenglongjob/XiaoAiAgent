from base64 import b64encode
from hashlib import md5, sha1
from json import dumps, loads
from os import makedirs, path
from random import sample
from string import ascii_letters, digits
from urllib import parse
from aiofiles import open as aio_open

from logging import getLogger
_LOGGER = getLogger(__package__)


def get_random(length):
    return ''.join(sample(ascii_letters + digits, length))


class MiTokenStore:

    def __init__(self, token_path):
        self.token_path = token_path

    async def load_token(self):
        if path.isfile(self.token_path):
            try:
                async with aio_open(self.token_path) as f:
                    token = loads(await f.read())
                    _LOGGER.info(
                        "Loaded Xiaomi token cache from %s (deviceId=%s, sidTokens=%s)",
                        self.token_path, bool(token.get('deviceId')), len(token.keys() - {'deviceId', 'userId', 'passToken'}),
                    )
                    return token
            except Exception as e:
                _LOGGER.exception("Exception on load token from %s: %s", self.token_path, e)
        return None

    async def save_token(self, token=None):
        if token:
            try:
                parent = path.dirname(self.token_path)
                if parent:
                    makedirs(parent, exist_ok=True)
                async with aio_open(self.token_path, 'w') as f:
                    await f.write(dumps(token, indent=2))
                _LOGGER.info(
                    "Saved Xiaomi token cache to %s (deviceId=%s, complete=%s)",
                    self.token_path, bool(token.get('deviceId')), bool(token.get('userId') and token.get('passToken')),
                )
            except Exception as e:
                _LOGGER.exception("Exception on save token to %s: %s", self.token_path, e)


class MiAccount:

    def __init__(self, session, username, password, token_store='data/.mi.token'):
        self._session = session
        self.username = username
        self.password = password
        self.token_store = MiTokenStore(token_store) if isinstance(token_store, str) else token_store
        self.token = None
        self._last_login_error = None

    def request(self, url, method='GET', **kwargs):
        if self._session:
            return self._session.request(method, url, **kwargs)

        class RequestContextManager:
            async def __aenter__(self):
                from aiohttp import ClientSession
                self.sess = ClientSession()
                self.resp = await self.sess.request(method, url, **kwargs)
                return self.resp

            async def __aexit__(self, exc_type, exc, tb):
                await self.resp.release()
                await self.sess.close()
        return RequestContextManager()

    async def login(self, sid):
        if not self.token:
            self.token = {'deviceId': get_random(16).upper()}
            _LOGGER.info("Created Xiaomi deviceId for login")
        else:
            _LOGGER.info("Reusing Xiaomi deviceId from token cache")
        try:
            resp = await self._serviceLogin(f'serviceLogin?sid={sid}&_json=true')
            if resp['code'] != 0:
                data = {
                    '_json': 'true',
                    'qs': resp['qs'],
                    'sid': resp['sid'],
                    '_sign': resp['_sign'],
                    'callback': resp['callback'],
                    'user': self.username,
                    'hash': md5(self.password.encode()).hexdigest().upper()
                }
                resp = await self._serviceLogin('serviceLoginAuth2', data)
                if resp['code'] != 0:
                    code = resp.get('code')
                    desc = resp.get('description', '') or resp.get('desc', '')
                    if code == 70016:
                        raise Exception(f'小米账号或密码错误 (code={code})')
                    elif code == 87001:
                        raise Exception(f'需要验证码，请在浏览器登录一次后重试 (code={code})')
                    elif '验证' in desc or 'captcha' in desc.lower():
                        raise Exception(f'登录需要验证: {desc} (code={code})')
                    else:
                        raise Exception(f'登录失败: {desc} (code={code})')

            security_status = resp.get('securityStatus')
            notification_url = resp.get('notificationUrl')
            location = resp.get('location')
            _LOGGER.info(
                "Xiaomi login response: code=%s securityStatus=%s notificationUrl=%s location=%s",
                resp.get('code'), security_status, bool(notification_url), bool(location),
            )
            if notification_url or security_status:
                verify_url = parse.urljoin(
                    'https://account.xiaomi.com', notification_url or '/'
                )
                self._last_login_error = (
                    f'小米账号需要安全验证 (securityStatus: {security_status})。'
                    f'请在浏览器打开：{verify_url} 完成验证后，请重新运行程序。'
                )
                print('小米账号需要安全验证')
                print(f'securityStatus: {security_status}')
                print(f'请在浏览器打开：{verify_url}')
                print('完成验证后，请重新运行程序。')
                if self.token_store:
                    await self.token_store.save_token(self.token)
                return False

            required = ('userId', 'passToken', 'location', 'nonce', 'ssecurity')
            missing = [key for key in required if not resp.get(key)]
            if missing:
                raise Exception(f'登录响应缺少必要字段: {", ".join(missing)}')
            self.token['userId'] = resp['userId']
            self.token['passToken'] = resp['passToken']

            serviceToken = await self._securityTokenService(resp['location'], resp['nonce'], resp['ssecurity'])
            self.token[sid] = (resp['ssecurity'], serviceToken)
            if self.token_store:
                await self.token_store.save_token(self.token)
            return True

        except Exception as e:
            self._last_login_error = str(e)
            if self.token_store:
                # Keep deviceId so the next attempt is recognized as the same device.
                await self.token_store.save_token(self.token)
            _LOGGER.exception("Exception on login %s: %s", self.username, e)
            return False

    async def _serviceLogin(self, uri, data=None):
        headers = {'User-Agent': 'APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS'}
        cookies = {'sdkVersion': '3.9', 'deviceId': self.token['deviceId']}
        if 'passToken' in self.token:
            cookies['userId'] = self.token['userId']
            cookies['passToken'] = self.token['passToken']
        url = 'https://account.xiaomi.com/pass/' + uri
        async with self.request(url, 'GET' if data is None else 'POST', data=data, cookies=cookies, headers=headers) as r:
            raw = await r.read()
            resp = loads(raw[11:])
            return resp

    async def _securityTokenService(self, location, nonce, ssecurity):
        nsec = 'nonce=' + str(nonce) + '&' + ssecurity
        clientSign = b64encode(sha1(nsec.encode()).digest()).decode()
        async with self.request(location + '&clientSign=' + parse.quote(clientSign)) as r:
            serviceToken = r.cookies['serviceToken'].value
            if not serviceToken:
                raise Exception(await r.text())
            return serviceToken

    async def mi_request(self, sid, url, data, headers, relogin=True):
        if self.token is None and self.token_store is not None:
            self.token = await self.token_store.load_token()
        if (self.token and sid in self.token) or await self.login(sid):
            cookies = {'userId': self.token['userId'], 'serviceToken': self.token[sid][1]}
            content = data(self.token, cookies) if callable(data) else data
            method = 'GET' if data is None else 'POST'
            async with self.request(url, method, data=content, cookies=cookies, headers=headers) as r:
                status = r.status
                if status == 200:
                    resp = await r.json(content_type=None)
                    code = resp['code']
                    if code == 0:
                        return resp
                    if 'auth' in resp.get('message', '').lower():
                        status = 401
                else:
                    resp = await r.text()
                if status == 401 and relogin:
                    _LOGGER.warning("Auth error on request %s %s, relogin...", url, resp)
                    self.token = {'deviceId': self.token.get('deviceId')} if self.token else None
                    if self.token_store:
                        await self.token_store.save_token(self.token)
                    return await self.mi_request(sid, url, data, headers, False)
        else:
            err = self._last_login_error or "Login failed"
            raise Exception(err)
        raise Exception(f"Error {url}: {resp}")
