"""Time prediction: base + steps*2s + elements*0.5s, corrected by historical data."""
from sqlalchemy.orm import Session

from app.config import settings
from app.models.test_run import TestRun, TimePrediction


def predict_duration(db: Session, user_id: str, num_steps: int, num_elements: int = 0) -> int:
    base = 5
    steps_seconds = num_steps * 2
    elements_seconds = num_elements * 0.5

    # Historical correction: average(actual/predicted) across user's runs
    runs = (
        db.query(TestRun)
        .filter(TestRun.owner_id == user_id, TestRun.status.in_(["passed", "failed"]))
        .all()
    )
    ratios = []
    for r in runs[-20:]:
        tp = db.query(TimePrediction).filter(TimePrediction.test_run_id == r.id).first()
        if tp and tp.predicted_seconds and r.duration_seconds:
            ratios.append(r.duration_seconds / tp.predicted_seconds)

    factor = 1.0
    if ratios:
        factor = sum(ratios) / len(ratios)
        factor = max(0.5, min(factor, 2.0))

    predicted = int((base + steps_seconds + elements_seconds) * factor)
    return max(10, predicted)


def save_prediction(db: Session, test_run_id: str, predicted: int, actual: int = 0,
                    num_steps: int = 0, num_elements: int = 0, factor: float = 1.0) -> None:
    db.add(TimePrediction(
        test_run_id=test_run_id,
        predicted_seconds=predicted,
        actual_seconds=actual,
        base_seconds=5,
        steps_seconds=num_steps * 2,
        elements_seconds=int(num_elements * 0.5),
        correction_factor=factor,
    ))
    db.commit()