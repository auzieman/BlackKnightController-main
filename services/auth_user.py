from flask_login import UserMixin


class BKCUser(UserMixin):
    __slots__ = ("id",)

    def __init__(self, user_id: int):
        self.id = int(user_id)
