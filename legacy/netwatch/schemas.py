from pydantic import BaseModel, HttpUrl


class ServiceCreate(BaseModel):
    name: str
    url: HttpUrl


class ServiceResponse(BaseModel):
    id: int
    name: str
    url: str
    is_up: bool

    model_config = {
        "from_attributes": True
    }