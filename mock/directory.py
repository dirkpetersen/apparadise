"""A fake Active Directory with the real authenticator interface.

`DemoDirectory` implements `authenticate` / `resolve` / `verify` like
`apparadise.ldapauth.AdAuthenticator`, so the real /api/login flow, throttle,
sessions and CSRF run unchanged. Used by mock/demo.py and the tests only.
Logins match like AD's filter: username or email, case-insensitively.
"""

from __future__ import annotations

import secrets

from apparadise.ldapauth import AuthUser, InvalidCredentials, Resolved

DEMO_PASSWORD = "demo"
DOMAIN = "demo.example"
PLACEHOLDER_LDAP_URL = "ldaps://directory.demo.invalid:636"  # never contacted

# sAMAccountName -> (display name, sysadmin, i.e. member of RCO-Admins)
DEMO_USERS = {
    "alice": ("Alice Anderson", False),
    "bob": ("Bob Brown", False),
    "dana": ("Dana Diaz", True),
}


class DemoDirectory:
    def __init__(self, password: str = DEMO_PASSWORD, users: dict | None = None):
        self.password = password
        self.users = {
            sam: AuthUser(sam, display, f"{sam}@{DOMAIN}", admin)
            for sam, (display, admin) in (users or DEMO_USERS).items()
        }

    def _find(self, login: str) -> AuthUser:
        key = login.strip().lower()
        if not key:
            raise InvalidCredentials("empty_login")
        for user in self.users.values():
            if key in (user.username.lower(), user.email.lower()):
                return user
        raise InvalidCredentials("unknown_user")

    async def authenticate(self, login: str, password: str) -> AuthUser:
        return await self.verify(await self.resolve(login, password), password)

    async def resolve(self, login: str, password: str) -> Resolved:
        if not password or not password.strip():
            raise InvalidCredentials("empty_password")
        user = self._find(login)
        return Resolved(dn=f"CN={user.username},OU=Demo", url=PLACEHOLDER_LDAP_URL, user=user)

    async def lookup_user(self, login: str) -> AuthUser:
        return self._find(login)

    async def verify(self, resolved: Resolved, password: str) -> AuthUser:
        if not secrets.compare_digest(password.encode(), self.password.encode()):
            raise InvalidCredentials("bad_password")
        return resolved.user
