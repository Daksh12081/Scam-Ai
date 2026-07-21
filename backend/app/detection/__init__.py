"""Public API for the layer-1 tactic detector -- import from here."""
from app.detection.detector import TacticEvent, detect_tactics

__all__ = ["TacticEvent", "detect_tactics"]
