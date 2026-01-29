# Password Hash Migration Guide

## Issue
When migrating from the old backend to backend_2, users with passwords hashed using bcrypt could not login because the new backend only supported argon2 hashing.

## Solution
The password context in `backend/app/core/security.py` has been updated to support both argon2 and bcrypt hashing schemes:

```python
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
```

## Behavior

### For Imported Users (bcrypt passwords)
- Old bcrypt password hashes are automatically recognized and verified
- Users can login with their existing passwords
- No password reset required

### For New Users
- All new passwords are hashed using argon2 (the first/preferred scheme)
- Argon2 is more secure and is the recommended modern hashing algorithm

### Automatic Migration (Optional)
Passlib can automatically rehash passwords to argon2 on successful login. To enable this:

```python
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    argon2__rounds=3,  # Configure argon2 parameters
)
```

When a user with a bcrypt password logs in, the system can detect it's using a deprecated scheme and automatically rehash it to argon2 after successful verification.

## Dependencies
- `passlib[argon2,bcrypt]>=1.7,<2`
- `bcrypt>=4.0,<5.0` (version 4.x is required for passlib compatibility)

## References
- Issue: User reported `passlib.exc.UnknownHashError: hash could not be identified` when trying to login
- Solution PR: [Insert PR link]
- Related files:
  - `backend/app/core/security.py`
  - `backend/pyproject.toml`
  - `backend/tests/test_bcrypt_compatibility.py`
