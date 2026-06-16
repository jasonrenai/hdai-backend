"""
Singleton dependencies for resource management.
Prevents creating multiple heavy resources on every API request.
"""
from typing import Optional

# Global singletons - Services
_auth_service = None
_user_management_service = None
_property_service = None
_filter_preset_service = None
_export_service = None
_listings_service = None
_operator_service = None
_pricelabs_service = None
_pricelabs_admin_service = None
_airbnb_admin_service = None
_booking_admin_service = None
_competitor_comparison_service = None
_competitor_property_service = None
_profile_service = None
_common_service = None
_background_mapping_service = None
_image_caption_service = None
_booking_service = None
_airbnb_service = None
_image_analysis_helper = None
_temporary_competitor_service = None
_deployment_cues_service = None
_cue_properties_service = None
_onboarding_status_service = None
_queue_status_service = None
_analytics_cues_preset_service = None
_excel_schedule_service = None
_speaker_profile_model = None
_speaker_topics_model = None
_speaker_target_audience_model = None
_delivery_modes_model = None
_speaking_formats_model = None
_chat_session_model = None
_speaker_profile_chatbot_service = None
_user_model = None
_scraper_service = None
_url_scraper_rapidapi_service = None
_google_query_scraper_service = None
_opportunity_service = None
_matched_opportunities_email_service = None
_opportunity_email_content_service = None
_opportunity_application_content_service = None
_opportunity_activity_service = None
_subscription_service = None
_email_service = None
_bio_document_summary_service = None
_bio_document_summary_model = None


def get_auth_service():
    """Get singleton AuthService instance"""
    global _auth_service
    if _auth_service is None:
        from app.services.Auth import AuthService
        _auth_service = AuthService()
    return _auth_service


def get_user_management_service():
    """Get singleton UserManagementService instance"""
    global _user_management_service
    if _user_management_service is None:
        from app.services.UserManagement import UserManagementService
        _user_management_service = UserManagementService()
    return _user_management_service


def get_profile_service():
    """Get singleton ProfileService instance"""
    global _profile_service
    if _profile_service is None:
        from app.services.Profile import ProfileService
        _profile_service = ProfileService()
    return _profile_service


def get_common_service():
    """Get singleton CommonService instance"""
    global _common_service
    if _common_service is None:
        from app.services.Common import CommonService
        _common_service = CommonService()
    return _common_service




def get_speaker_profile_model():
    """Get singleton SpeakerProfileModel instance"""
    global _speaker_profile_model
    if _speaker_profile_model is None:
        from app.models.SpeakerProfile import SpeakerProfileModel
        _speaker_profile_model = SpeakerProfileModel()
    return _speaker_profile_model


def get_speaker_topics_model():
    """Get singleton SpeakerTopicsModel instance"""
    global _speaker_topics_model
    if _speaker_topics_model is None:
        from app.models.SpeakerTopics import SpeakerTopicsModel
        _speaker_topics_model = SpeakerTopicsModel()
    return _speaker_topics_model


def get_speaker_target_audience_model():
    """Get singleton SpeakerTargetAudienceModel instance"""
    global _speaker_target_audience_model
    if _speaker_target_audience_model is None:
        from app.models.SpeakerTargetAudience import SpeakerTargetAudienceModel
        _speaker_target_audience_model = SpeakerTargetAudienceModel()
    return _speaker_target_audience_model


def get_delivery_modes_model():
    """Get singleton SpeakerDeliveryModesModel instance"""
    global _delivery_modes_model
    if _delivery_modes_model is None:
        from app.models.SpeakerDeliveryModes import SpeakerDeliveryModesModel
        _delivery_modes_model = SpeakerDeliveryModesModel()
    return _delivery_modes_model


def get_speaker_formats_model():
    """Get singleton SpeakerSpeakingFormatsModel instance"""
    global _speaking_formats_model
    if _speaking_formats_model is None:
        from app.models.SpeakerSpeakingFormats import SpeakerSpeakingFormatsModel
        _speaking_formats_model = SpeakerSpeakingFormatsModel()
    return _speaking_formats_model


def get_user_model():
    """Get singleton UserModel instance (users collection)."""
    global _user_model
    if _user_model is None:
        from app.models.User import UserModel
        _user_model = UserModel()
    return _user_model


def get_chat_session_model():
    """Get singleton ChatSessionModel instance"""
    global _chat_session_model
    if _chat_session_model is None:
        from app.models.ChatSession import ChatSessionModel
        _chat_session_model = ChatSessionModel()
    return _chat_session_model


def get_speaker_profile_chatbot_service():
    """Get singleton SpeakerProfileChatbotService instance"""
    global _speaker_profile_chatbot_service
    if _speaker_profile_chatbot_service is None:
        from app.services.SpeakerProfileChatbotService import SpeakerProfileChatbotService
        _speaker_profile_chatbot_service = SpeakerProfileChatbotService(
            get_speaker_profile_model(),
            get_speaker_topics_model(),
            get_speaker_target_audience_model(),
            get_delivery_modes_model(),
            get_speaker_formats_model(),
            get_chat_session_model(),
            get_user_model(),
        )
    return _speaker_profile_chatbot_service


def get_scraper_service():
    """Get singleton ScraperRapidAPIService instance (RapidAPI scraper + LLM)."""
    global _scraper_service
    if _scraper_service is None:
        from app.services.Scraper import ScraperService
        _scraper_service = ScraperService()
    return _scraper_service


def get_url_scraper_rapidapi_service():
    """Get singleton UrlScraperRapidAPIService instance (UrlCollection + Opportunities flow)."""
    global _url_scraper_rapidapi_service
    if _url_scraper_rapidapi_service is None:
        from app.services.UrlScraperRapidAPI import UrlScraperRapidAPIService
        _url_scraper_rapidapi_service = UrlScraperRapidAPIService()
    return _url_scraper_rapidapi_service


def get_google_query_scraper_service():
    """Get singleton GoogleQueryScraperService instance (GoogleQueries + SERP + UrlCollection flow)."""
    global _google_query_scraper_service
    if _google_query_scraper_service is None:
        from app.services.GoogleQueryScraper import GoogleQueryScraperService
        _google_query_scraper_service = GoogleQueryScraperService()
    return _google_query_scraper_service


def get_opportunity_service():
    """Get singleton OpportunityService instance."""
    global _opportunity_service
    if _opportunity_service is None:
        from app.services.Opportunity import OpportunityService
        _opportunity_service = OpportunityService()
    return _opportunity_service


def get_subscription_service():
    global _subscription_service
    if _subscription_service is None:
        from app.services.Subscriptions import SubscriptionsService

        _subscription_service = SubscriptionsService()
    return _subscription_service


def get_bio_document_summary_model():
    """Get singleton BioDocumentSummaryModel instance."""
    global _bio_document_summary_model
    if _bio_document_summary_model is None:
        from app.models.BioDocumentSummary import BioDocumentSummaryModel
        _bio_document_summary_model = BioDocumentSummaryModel()
    return _bio_document_summary_model


def get_bio_document_summary_service():
    """Get singleton BioDocumentSummaryService instance."""
    global _bio_document_summary_service
    if _bio_document_summary_service is None:
        from app.services.BioDocumentSummaryService import BioDocumentSummaryService
        _bio_document_summary_service = BioDocumentSummaryService(
            speaker_profile_model=get_speaker_profile_model(),
            bio_document_summary_model=get_bio_document_summary_model(),
        )
    return _bio_document_summary_service


def get_email_service():
    """Get singleton EmailService instance."""
    global _email_service
    if _email_service is None:
        from app.email.service import EmailService
        _email_service = EmailService()
    return _email_service


def get_matched_opportunities_email_service():
    """Get singleton MatchedOpportunitiesEmailService instance."""
    global _matched_opportunities_email_service
    if _matched_opportunities_email_service is None:
        from app.services.MatchedOpportunitiesEmailService import MatchedOpportunitiesEmailService
        _matched_opportunities_email_service = MatchedOpportunitiesEmailService(
            opportunity_service=get_opportunity_service(),
            speaker_profile_model=get_speaker_profile_model(),
        )
    return _matched_opportunities_email_service


def get_opportunity_email_content_service():
    """Get singleton OpportunityEmailContentService instance."""
    global _opportunity_email_content_service
    if _opportunity_email_content_service is None:
        from app.services.OpportunityEmailContent import OpportunityEmailContentService
        _opportunity_email_content_service = OpportunityEmailContentService(
            speaker_profile_model=get_speaker_profile_model(),
            opportunity_model=get_opportunity_service().model,
        )
    return _opportunity_email_content_service


def get_opportunity_application_content_service():
    """Get singleton OpportunityApplicationContentService instance."""
    global _opportunity_application_content_service
    if _opportunity_application_content_service is None:
        from app.services.OpportunityApplicationContent import OpportunityApplicationContentService
        _opportunity_application_content_service = OpportunityApplicationContentService(
            speaker_profile_model=get_speaker_profile_model(),
            opportunity_model=get_opportunity_service().model,
        )
    return _opportunity_application_content_service


def get_opportunity_activity_service():
    """Get singleton OpportunityActivityService instance."""
    global _opportunity_activity_service
    if _opportunity_activity_service is None:
        from app.services.OpportunityActivity import OpportunityActivityService
        _opportunity_activity_service = OpportunityActivityService()
    return _opportunity_activity_service


def cleanup_resources():
    """
    Cleanup all singleton resources. Call this on application shutdown.
    """
    global _auth_service, _property_service, _filter_preset_service, _export_service
    global _listings_service, _operator_service, _pricelabs_service, _pricelabs_admin_service
    global _airbnb_admin_service, _booking_admin_service, _competitor_comparison_service
    global _competitor_property_service, _profile_service, _common_service
    global _background_mapping_service, _image_caption_service, _booking_service, _airbnb_service
    global _image_analysis_helper, _temporary_competitor_service, _deployment_cues_service
    global _image_analysis_helper, _temporary_competitor_service, _cue_properties_service
    global _onboarding_status_service, _queue_status_service, _analytics_cues_preset_service, _excel_schedule_service, _speaker_profile_model, _speaker_topics_model, _speaker_target_audience_model, _delivery_modes_model, _speaking_formats_model, _chat_session_model, _speaker_profile_chatbot_service, _scraper_service, _url_scraper_rapidapi_service, _google_query_scraper_service, _opportunity_service, _matched_opportunities_email_service, _opportunity_email_content_service, _opportunity_application_content_service, _opportunity_activity_service, _user_management_service, _subscription_service, _email_service, _bio_document_summary_service, _bio_document_summary_model

    # Reset all services
    _auth_service = None
    _user_management_service = None
    _property_service = None
    _filter_preset_service = None
    _export_service = None
    _listings_service = None
    _operator_service = None
    _pricelabs_service = None
    _pricelabs_admin_service = None
    _airbnb_admin_service = None
    _booking_admin_service = None
    _competitor_comparison_service = None
    _competitor_property_service = None
    _profile_service = None
    _common_service = None
    _background_mapping_service = None
    _image_caption_service = None
    _booking_service = None
    _airbnb_service = None
    _image_analysis_helper = None
    _temporary_competitor_service = None
    _deployment_cues_service = None
    _cue_properties_service = None
    _onboarding_status_service = None
    _queue_status_service = None
    _analytics_cues_preset_service = None
    _excel_schedule_service = None
    _speaker_profile_model = None
    _speaker_topics_model = None
    _speaker_target_audience_model = None
    _delivery_modes_model = None
    _speaking_formats_model = None
    _chat_session_model = None
    _speaker_profile_chatbot_service = None
    _user_model = None
    _scraper_service = None
    _url_scraper_rapidapi_service = None
    _google_query_scraper_service = None
    _opportunity_service = None
    _matched_opportunities_email_service = None
    _opportunity_email_content_service = None
    _opportunity_application_content_service = None
    _opportunity_activity_service = None
    _subscription_service = None
    _email_service = None
    _bio_document_summary_service = None
    _bio_document_summary_model = None
