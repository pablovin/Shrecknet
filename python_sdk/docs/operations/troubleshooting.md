# Troubleshooting

## Import errors in examples

If relative imports fail, run examples from repo root:

```bash
python python_sdk/examples/01_login_and_user_creation/02_register_and_login.py
```

## 401 Unauthorized

- Ensure user exists and password is correct.
- Run registration-first example to bootstrap first user.

## 409 Conflict on user registration

Expected when user/email already exists. Bootstrap helper handles this.

## 422 Validation errors

Check payload shape and required fields in the generated API reference.

## Connection errors

Ensure Docker stack is running and API is reachable at `http://localhost:8100`.
