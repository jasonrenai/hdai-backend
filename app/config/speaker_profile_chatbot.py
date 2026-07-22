"""
Allowed values for Speaker Profile Chatbot (conversation-based onboarding).
Topics and Target Audiences are validated against these names; DB lookups provide _id/slug.
"""

# Canonical allowed topic names (exact match, case-sensitive for storage)
TOPICS = [
    "AI",
    "B2B",
    "B2C",
    "Communications",
    "Customer Experience",
    "Data Science",
    "Developer",
    "E-Commerce",
    "EdTech",
    "Education",
    "Entrepreneurship",
    "Executive Leadership",
    "Financial Services",
    "Franchise",
    "Health",
    "Human Resources (HR)",
    "Marketing",
    "Nonprofit",
    "Public Relations (PR)",
    "Remortgage",
    "Retail",
    "Technology",
    "UX/UI",
    "Women In Tech",
]

# Canonical allowed speaking formats
SPEAKING_FORMATS = [
    "Breakout Session",
    "Keynote",
    "Panel Discussion",
    "Solo Talk",
    "Workshop",
]

# Canonical allowed delivery modes (matches speaker_profile_steps)
DELIVERY_MODE = [
    "Virtual",
    "In-person",
    "Hybrid",
]

# Canonical allowed target audience names
TARGET_AUDIENCES = [
    "Corporate Teams",
    "Entrepreneurs",
    "Executives",
    "Franchise",
    "General Audience",
    "HR Professionals",
    "Managers",
    "Nonprofit Leaders",
    "Sales Teams",
    "Small Businesses",
    "Startups",
    "Students",
    "Technical Professionals",
    "Women Leaders",
]

# Mandatory fields for speaker profile (must be filled before "complete")
# email and full_name are required for initial creation
MANDATORY_FIELDS = [
    "full_name",
    "email",
]

# Optional fields (asked after profile create; not required to set isCompleted)
OPTIONAL_FIELDS = [
    "talk_description",
    "key_takeaways",
    "linkedin_url",
    "bio",
    "professional_memberships",
    "past_speaking_examples",
    "video_links",
    "testimonial",
    "name_salutation",
    "twitter",
    "facebook",
    "instagram",
    "address_city",
    "address_state",
    "address_country",
    "phone_number",
    "preferred_speaking_time",
    "topics",
    "speaking_formats",
    "delivery_mode",
    "target_audiences",
]

# Human-readable labels for mandatory fields (used in user-facing messages)
MANDATORY_FIELDS_DISPLAY = {
    "full_name": "Full name",
    "email": "Email",
}

# Human-readable labels for optional fields (used in LLM prompts / user-facing messages)
OPTIONAL_FIELDS_DISPLAY = {
    "talk_description": "Talk description",
    "key_takeaways": "Key takeaways",
    "linkedin_url": "Social media URLs",
    "bio": "Bio",
    "professional_memberships": "Professional memberships",
    "past_speaking_examples": "Past speaking examples",
    "video_links": "Video links",
    "testimonial": "Testimonial",
    "name_salutation": "Name salutation",
    "twitter": "Twitter",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "address_city": "Address city",
    "address_state": "Address state",
    "address_country": "Address country",
    "phone_number": "Phone number",
    "preferred_speaking_time": "Preferred speaking time",
    "topics": "Topics",
    "speaking_formats": "Speaking formats",
    "delivery_mode": "Delivery mode",
    "target_audiences": "Target audiences",
}
