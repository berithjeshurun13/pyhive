from sqlmodel import SQLModel, Field


class Tools(SQLModel, table=True) :
    id : str = Field(primary_key=True)
    