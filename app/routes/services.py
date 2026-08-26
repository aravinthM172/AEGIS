from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Service
from app.schemas import ServiceCreate, ServiceResponse
from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas import ServiceCreate, ServiceResponse
from app.services.monitor import check_service

router = APIRouter(
    prefix="/api/v1/services",
    tags=["Services"]
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.post(
    "",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED
)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(get_db)
):
    new_service = Service(
        name=service.name,
        url=str(service.url)
    )

    db.add(new_service)
    db.commit()
    db.refresh(new_service)

    return new_service

@router.get(
    "",
    response_model=list[ServiceResponse]
)
def get_services(
    db: Session = Depends(get_db)
):
    return db.query(Service).all()
@router.get(
    "/{service_id}",
    response_model=ServiceResponse
)
def get_service(
    service_id: int,
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service not found"
        )

    return service
@router.delete(
    "/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_service(
    service_id: int,
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service not found"
        )

    db.delete(service)
    db.commit()

    return None
@router.put(
    "/{service_id}",
    response_model=ServiceResponse
)
def update_service(
    service_id: int,
    service_data: ServiceCreate,
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service not found"
        )

    service.name = service_data.name
    service.url = str(service_data.url)

    db.commit()
    db.refresh(service)

    return service
@router.post(
    "/{service_id}/check"
)
async def monitor_service(
    service_id: int,
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)

    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Service not found"
        )

    result = await check_service(
        service,
        db
    )

    return {
        "service_id": result.service_id,
        "is_up": result.is_up,
        "status_code": result.status_code,
        "response_time_ms": result.response_time_ms,
        "error": result.error,
        "checked_at": result.checked_at
    }