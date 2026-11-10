"""Central hard stop for face recognition while model commercial rights are unresolved.

Do not change this to False until commercial model licensing is documented,
privacy/legal review is complete, and the re-enable change has passed tests.
"""
FACE_RECOGNITION_TEMPORARILY_DISABLED = True


def is_face_recognition_allowed(settings) -> bool:
    """One policy for API, worker and startup warmups; never read face profiles while paused."""
    if FACE_RECOGNITION_TEMPORARILY_DISABLED:
        return False
    return not settings.is_production or (
        settings.face_recognition_legal_reviewed
        and settings.face_model_commercial_license_confirmed
    )
