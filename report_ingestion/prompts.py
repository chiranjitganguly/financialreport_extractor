from langchain_core.prompts import ChatPromptTemplate

# Company name extraction — used in classifiers/company_name.py
COMPANY_NAME_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a financial document analyst. Extract the reporting entity's "
            "legal or common name exactly as it appears on the cover or title page. "
            "Do not return a subsidiary name, auditor name, or any other organisation "
            "mentioned in the document.",
        ),
        (
            "human",
            "Document excerpt:\n\n{excerpt}\n\n"
            "Return the reporting entity name and your confidence (0.0–1.0).",
        ),
    ]
)

# Batched classification fallback — used in fallback.py
CLASSIFICATION_FALLBACK_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a financial document classifier. Given a document excerpt, "
            "classify only the fields listed in the user message. Leave other fields null.",
        ),
        (
            "human",
            "Document excerpt:\n\n{excerpt}\n\n"
            "Classify the following fields: {needed_fields}.\n"
            "Valid industries: {valid_industries}.\n"
            "Return your best guess and confidence (0.0–1.0) for each requested field.",
        ),
    ]
)
