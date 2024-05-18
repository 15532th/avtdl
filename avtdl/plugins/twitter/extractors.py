from pydantic import BaseModel

from avtdl.core.utils import find_one


class UserInfo(BaseModel):
    rest_id: str
    handle: str
    name: str
    description: str
    avatar_url: str
    banner_url: str
    location: str

    @classmethod
    def from_data(cls, data: dict) -> 'UserInfo':
        result = find_one(data, '$.data.user.result')
        if result is None:
            raise ValueError(f'failed to parse data into {cls.__name__}: no "result" property')
        return cls.from_result(result)

    @classmethod
    def from_result(cls, result: dict) -> 'UserInfo':
        typename = result.get('__typename')
        if typename != 'User':
            raise ValueError(f'failed to parse result into {cls.__name__}: __typename is "{typename}, expected "User"')
        rest_id = result['rest_id']

        legacy = result['legacy']

        handle = legacy['screen_name']
        name = legacy['name']
        description = legacy['description']
        avatar_url = legacy['profile_image_url_https'].replace('_normal', '_400x400')
        banner_url = legacy['profile_banner_url']
        location = legacy['location']
        return cls(rest_id=rest_id, handle=handle, name=name, description=description, avatar_url=avatar_url, banner_url=banner_url, location=location)
