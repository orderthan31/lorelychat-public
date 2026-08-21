from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.model_providers import (
    ModelOptionManualCreate,
    ModelOptionRead,
    ModelOptionTestRead,
    ModelOptionUpdate,
    ProviderAccountCreate,
    ProviderAccountRead,
    ProviderAccountTestRead,
    ProviderAccountUpdate,
)
from app.services import model_provider_service

router = APIRouter(tags=["model-providers"])


def _not_found(error: ValueError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.get("/model-provider-accounts", response_model=list[ProviderAccountRead])
def list_provider_accounts(session: Session = Depends(get_session)):
    return model_provider_service.list_accounts(session)


@router.post("/model-provider-accounts", response_model=ProviderAccountRead)
def create_provider_account(payload: ProviderAccountCreate, session: Session = Depends(get_session)):
    try:
        account = model_provider_service.create_account(session, payload)
    except ValueError as error:
        raise _bad_request(error) from error
    return model_provider_service.serialize_account(account)


@router.patch("/model-provider-accounts/{provider_account_id}", response_model=ProviderAccountRead)
def update_provider_account(provider_account_id: str, payload: ProviderAccountUpdate, session: Session = Depends(get_session)):
    try:
        account = model_provider_service.update_account(session, provider_account_id, payload)
    except ValueError as error:
        if "cannot be used together" in str(error):
            raise _bad_request(error) from error
        raise _not_found(error) from error
    return model_provider_service.serialize_account(account)


@router.post("/model-provider-accounts/{provider_account_id}/test", response_model=ProviderAccountTestRead)
def test_provider_account(provider_account_id: str, session: Session = Depends(get_session)):
    try:
        return model_provider_service.test_account(session, provider_account_id)
    except ValueError as error:
        raise _not_found(error) from error


@router.post("/model-provider-accounts/{provider_account_id}/sync-models", response_model=list[ModelOptionRead])
def sync_provider_models(provider_account_id: str, session: Session = Depends(get_session)):
    try:
        return model_provider_service.sync_models(session, provider_account_id)
    except ValueError as error:
        if "not found" in str(error).lower():
            raise _not_found(error) from error
        raise _bad_request(error) from error


@router.get("/model-options", response_model=list[ModelOptionRead])
def list_model_options(session: Session = Depends(get_session)):
    return model_provider_service.list_options(session)


@router.post("/model-options/manual", response_model=ModelOptionRead)
def create_manual_model_option(payload: ModelOptionManualCreate, session: Session = Depends(get_session)):
    try:
        option = model_provider_service.create_manual_option(session, payload)
        account = model_provider_service._account_by_id(session, option.provider_account_id)
    except ValueError as error:
        raise _not_found(error) from error
    return model_provider_service.serialize_option(option, account)


@router.patch("/model-options/{model_option_id}", response_model=ModelOptionRead)
def update_model_option(model_option_id: str, payload: ModelOptionUpdate, session: Session = Depends(get_session)):
    try:
        option = model_provider_service.update_option(session, model_option_id, payload)
        account = model_provider_service._account_by_id(session, option.provider_account_id)
    except ValueError as error:
        raise _not_found(error) from error
    return model_provider_service.serialize_option(option, account)


@router.post("/model-options/{model_option_id}/test", response_model=ModelOptionTestRead)
def test_model_option(model_option_id: str, session: Session = Depends(get_session)):
    try:
        return model_provider_service.test_option(session, model_option_id)
    except ValueError as error:
        raise _not_found(error) from error
