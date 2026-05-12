from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_email_service
from app.email.enums import EmailEventType
from app.helpers.Utilities import Utils
from app.schemas.Email import EmailEventTestRequest, SendSpecificEmailRequest
from app.schemas.ServerResponse import ServerResponse

router = APIRouter(prefix="/api/v1/email-test", tags=["Email Test"])


# @router.post("/send/event", response_model=ServerResponse)
# async def send_event_email(
#     body: SendEventByTypeRequest,
#     service=Depends(get_email_service),
# ):
#     try:
#         sent = service.send_event_email(
#             event_type=body.event_type,
#             to_email=str(body.to_email),
#             sender=body.sender,
#             template_model=_merge_test_template_model(body.event_type, body),
#         )
#         return Utils.create_response(
#             {
#                 "sent": sent,
#                 "event_type": body.event_type.value,
#                 "to_email": str(body.to_email),
#             },
#             True,
#         )
#     except Exception as e:
#         raise HTTPException(
#             status_code=400,
#             detail={"data": None, "error": str(e), "success": False},
#         )


@router.post("/send/welcome", response_model=ServerResponse)
async def send_welcome_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.WELCOME_EMAIL,
        body=body,
    )


@router.post("/send/password-reset", response_model=ServerResponse)
async def send_password_reset_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.PASSWORD_RESET,
        body=body,
    )


@router.post("/send/account-confirmation", response_model=ServerResponse)
async def send_account_confirmation_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.ACCOUNT_CONFIRMATION,
        body=body,
    )


@router.post("/send/system-notification", response_model=ServerResponse)
async def send_system_notification_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.SYSTEM_NOTIFICATION,
        body=body,
    )


@router.post("/send/alerts/pitch-ready", response_model=ServerResponse)
async def send_alert_pitch_ready_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.ALERT_PITCH_READY,
        body=body,
    )


@router.post("/send/alerts/new-opportunity", response_model=ServerResponse)
async def send_alert_new_opportunity_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.ALERT_NEW_OPPORTUNITY,
        body=body,
    )


@router.post("/send/alerts/submission-reminder", response_model=ServerResponse)
async def send_alert_submission_reminder_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.ALERT_SUBMISSION_REMINDER,
        body=body,
    )


@router.post("/send/alerts/deadline-approaching", response_model=ServerResponse)
async def send_alert_deadline_approaching_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.ALERT_DEADLINE_APPROACHING,
        body=body,
    )


@router.post("/send/support/customer-support", response_model=ServerResponse)
async def send_support_customer_support_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.SUPPORT_CUSTOMER_SUPPORT,
        body=body,
    )


@router.post("/send/support/help-request", response_model=ServerResponse)
async def send_support_help_request_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.SUPPORT_HELP_REQUEST,
        body=body,
    )


@router.post("/send/support/billing-question", response_model=ServerResponse)
async def send_support_billing_question_email(
    body: SendSpecificEmailRequest,
    service=Depends(get_email_service),
):
    return await _send_specific_event(
        service=service,
        event_type=EmailEventType.SUPPORT_BILLING_QUESTION,
        body=body,
    )


def _merge_test_template_model(
    event_type: EmailEventType,
    body: SendSpecificEmailRequest | EmailEventTestRequest,
) -> dict:
    merged = dict(body.template_model)
    if body.user_name:
        merged.setdefault("user_name", body.user_name)
    if body.cta_url:
        if event_type == EmailEventType.PASSWORD_RESET:
            merged.setdefault("reset_password_url", body.cta_url)
        elif event_type == EmailEventType.ACCOUNT_CONFIRMATION:
            merged.setdefault("verification_url", body.cta_url)
        else:
            merged.setdefault("cta_url", body.cta_url)
    return merged


async def _send_specific_event(service, event_type: EmailEventType, body: SendSpecificEmailRequest):
    try:
        sent = service.send_event_email(
            event_type=event_type,
            to_email=str(body.to_email),
            template_model=_merge_test_template_model(event_type, body),
        )
        return Utils.create_response(
            {
                "sent": sent,
                "event_type": event_type.value,
                "to_email": str(body.to_email),
            },
            True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )

