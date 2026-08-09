class UserNotProvisioned(Exception):
    def __init__(self) -> None:
        super().__init__("authenticated user is not provisioned")
