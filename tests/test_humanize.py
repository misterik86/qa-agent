"""Human-readable action descriptions."""
from app.services.humanize import humanize_step


def test_click_button_by_text():
    text = humanize_step("click", "button:has-text('Войти')")
    assert "Нажатие кнопки" in text and "Войти" in text
    assert "button" not in text


def test_click_link():
    text = humanize_step("click", "a:has-text('О компании')")
    assert "ссылке" in text and "О компании" in text
    assert "a:" not in text.replace("ссылке", "")


def test_click_and_back_link():
    text = humanize_step("click_and_back", "a:has-text('Тарифы')")
    assert "Тарифы" in text
    assert "возврат" in text or "доступности" in text


def test_field_by_name_friendly():
    text = humanize_step("validate_field", "form [name=\"email\"]",
                         value="not-an-email", expected_effect="validation_email")
    assert "email" in text
    assert "[name=" not in text
    assert "некорректный" in text.lower()


def test_validate_required():
    text = humanize_step("validate_field", "form [name=\"name\"]",
                         expected_effect="validation_required")
    assert "обязательно" in text
    assert "[name=" not in text


def test_fill_form_with_data_and_password_masking():
    form_data = [
        {"name": "email", "kind": "email", "value": "qa.test@example.com"},
        {"name": "password", "kind": "password", "value": "QaPass!2025"},
    ]
    shown = humanize_step("fill_form", "form", form_data=form_data, mask=False)
    assert "Заполнение формы валидными данными" in shown
    assert "qa.test@example.com" in shown
    assert "QaPass!2025" in shown

    masked = humanize_step("fill_form", "form", form_data=form_data, mask=True)
    assert "QaPass!2025" not in masked
    assert "qa.test@example.com" in masked  # non-secret values stay visible


def test_check_form_describes_submission():
    text = humanize_step("check_form", "form:nth-of-type(1)",
                         form_data=[{"name": "phone", "kind": "tel", "value": "+79000000000"}])
    assert "Отправка формы" in text
    assert "телефон" in text
    assert "+79000000000" in text


def test_select_action():
    text = humanize_step("select", "select[name=\"city\"]", value="Москва")
    assert "Москва" in text and "Выбор" in text


def test_fill_action_shows_value():
    text = humanize_step("fill", "input[name=\"login\"]", value="test@test.com")
    assert "test@test.com" in text
    assert "input[" not in text


def test_goto_and_scan():
    assert "example.com" in humanize_step("goto", "", value="https://example.com")
    assert humanize_step("scan") == "Сканирование страницы"
