"""Persistent test suites (regression sets) and their cases."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.ssrf import assert_public_url
from app.database import get_db
from app.models.suite import SiteCredential, TestCase, TestSuite
from app.models.test_run import TestRun
from app.models.user import User
from app.schemas.suite import CaseCreate, CaseOut, CasePatch, SuiteCreate, SuiteOut, SuitePatch

router = APIRouter(prefix="/api/suites", tags=["suites"])


def _suite_out(db: Session, s: TestSuite) -> SuiteOut:
    cases = db.query(TestCase).filter(TestCase.suite_id == s.id).all()
    last = (db.query(TestRun)
            .filter(TestRun.suite_id == s.id,
                    TestRun.status.notin_(["queued", "running"]))
            .order_by(TestRun.created_at.desc())
            .first())
    return SuiteOut(
        id=s.id, name=s.name, url=s.url,
        mode=s.mode if isinstance(s.mode, str) else s.mode.value,
        credential_id=s.credential_id,
        cases_count=len(cases),
        enabled_cases_count=sum(1 for c in cases if c.enabled),
        last_run_id=last.id if last else None,
        last_run_status=(last.status.value if hasattr(last.status, "value") else last.status) if last else None,
        last_run_at=last.created_at if last else None,
        created_at=s.created_at,
    )


def _get_suite(db: Session, suite_id: str, user: User) -> TestSuite:
    suite = db.query(TestSuite).filter(TestSuite.id == suite_id, TestSuite.owner_id == user.id).first()
    if not suite:
        raise HTTPException(status_code=404, detail="Набор не найден")
    return suite


@router.post("", response_model=SuiteOut, status_code=201)
def create_suite(body: SuiteCreate, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
    try:
        assert_public_url(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if body.mode not in ("specific", "full_page"):
        raise HTTPException(status_code=400, detail="Режим должен быть specific или full_page")
    suite = TestSuite(owner_id=user.id, name=body.name, url=body.url,
                      mode=body.mode, credential_id=body.credential_id)
    db.add(suite)
    db.commit()
    db.refresh(suite)
    return _suite_out(db, suite)


@router.get("", response_model=list[SuiteOut])
def list_suites(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    suites = db.query(TestSuite).filter(TestSuite.owner_id == user.id)\
        .order_by(TestSuite.updated_at.desc()).all()
    return [_suite_out(db, s) for s in suites]


@router.get("/{suite_id}", response_model=SuiteOut)
def get_suite(suite_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _suite_out(db, _get_suite(db, suite_id, user))


@router.get("/{suite_id}/cases", response_model=list[CaseOut])
def list_cases(suite_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    suite = _get_suite(db, suite_id, user)
    return db.query(TestCase).filter(TestCase.suite_id == suite.id)\
        .order_by(TestCase.order).all()


@router.patch("/{suite_id}", response_model=SuiteOut)
def patch_suite(suite_id: str, body: SuitePatch, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    suite = _get_suite(db, suite_id, user)
    if body.name is not None:
        suite.name = body.name
    if body.url is not None:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")
        try:
            assert_public_url(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        suite.url = body.url
    if body.mode is not None:
        if body.mode not in ("specific", "full_page"):
            raise HTTPException(status_code=400, detail="Недопустимый режим")
        suite.mode = body.mode
    if body.credential_id is not None:
        if body.credential_id == "":
            suite.credential_id = None
        else:
            if not db.query(SiteCredential).filter(
                    SiteCredential.id == body.credential_id, SiteCredential.owner_id == user.id).first():
                raise HTTPException(status_code=404, detail="Учётные данные не найдены")
            suite.credential_id = body.credential_id
    db.commit()
    db.refresh(suite)
    return _suite_out(db, suite)


@router.delete("/{suite_id}", status_code=204)
def delete_suite(suite_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    suite = _get_suite(db, suite_id, user)
    db.query(TestCase).filter(TestCase.suite_id == suite.id).delete()
    db.delete(suite)
    db.commit()


@router.post("/{suite_id}/cases", response_model=CaseOut, status_code=201)
def add_case(suite_id: str, body: CaseCreate, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    suite = _get_suite(db, suite_id, user)
    max_order = db.query(TestCase).filter(TestCase.suite_id == suite.id).count()
    case = TestCase(suite_id=suite.id, order=max_order, action=body.action,
                    selector=body.selector, value=body.value, expected=body.expected,
                    expected_effect=body.expected_effect, enabled=body.enabled, source="manual")
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.patch("/cases/{case_id}", response_model=CaseOut)
def patch_case(case_id: str, body: CasePatch, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    case = (db.query(TestCase).join(TestSuite)
            .filter(TestCase.id == case_id, TestSuite.owner_id == user.id).first())
    if not case:
        raise HTTPException(status_code=404, detail="Тест-кейс не найден")
    for field in ("action", "selector", "value", "expected", "expected_effect", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(case, field, val)
    if body.order is not None:
        case.order = body.order
    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    case = (db.query(TestCase).join(TestSuite)
            .filter(TestCase.id == case_id, TestSuite.owner_id == user.id).first())
    if not case:
        raise HTTPException(status_code=404, detail="Тест-кейс не найден")
    db.delete(case)
    db.commit()


@router.post("/{suite_id}/reorder")
def reorder_cases(suite_id: str, case_ids: list[str], user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Set new order by passing the full list of case ids in desired sequence."""
    suite = _get_suite(db, suite_id, user)
    cases = {c.id: c for c in db.query(TestCase).filter(TestCase.suite_id == suite.id).all()}
    for idx, cid in enumerate(case_ids):
        if cid in cases:
            cases[cid].order = idx
    db.commit()
    return {"ok": True}
