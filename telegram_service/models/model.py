from pydantic import BaseModel
from typing import Optional

class ReactionPayload(BaseModel):
    user_id: int
    reaction: str
    message: Optional[str] = None


from typing import Literal, List 

ChatType = List[Literal["group", "private", "channel"]]

class BaseTool:

    class EmptyArgs:
        pass


from pydantic import BaseModel

class ReactionRequest(BaseModel):
    user_id: int      
    access_hash: int    
    message_id: int    
    emotion: str       
    
    
class GeoRequest(BaseModel):
    user_id: int
    access_hash: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    caption: Optional[str] = None


class PhotoBinary(BaseModel):
    file_name: str
    content_base64: str


class PhotoSendRequest(BaseModel):
    user_id: int
    access_hash: int
    photos: List[PhotoBinary]
    description: Optional[str] = None


class SendMessageRequest(BaseModel):
    user_id: int
    message: str


class ResolveUserRequest(BaseModel):
    user_id: int
