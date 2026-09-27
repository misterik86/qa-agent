from app.services.validator import check_registration_intent, syntax_validate, validate_scenario
from app.core.ssrf import assert_public_url, is_private_host
from app.agent.dispatcher import Dispatcher


def test_syntax_validate_ok():
    text = 'Нажми на кнопку "Войти", введи в поле логин test@test.com, введи в поле пароль 123456, нажми "Отправить", проверь, что появилось сообщение "Добро пожаловать"'
    r = syntax_validate(text)
    assert r.valid is True


def test_syntax_validate_too_short():
    r = syntax_validate("нажми кнопку")
    assert r.valid is False
    assert any("5 слов" in e for e in r.errors)


def test_syntax_validate_no_verb():
    r = syntax_validate("кнопка входа и поле логина находятся на странице")
    assert r.valid is False
    assert any("глагол" in e for e in r.errors)


def test_syntax_validate_vague():
    r = syntax_validate("нажми кнопку и попробуй что-нибудь сделать с сайтом")
    assert r.valid is False
    assert any("расплывчат" in e for e in r.errors)


def test_registration_intent_detected():
    assert check_registration_intent("нажми кнопку и зарегистрируй нового пользователя") is True


def test_full_validation_with_login_requires_credentials():
    text = "нажми на кнопку Войти, проверь форму логина на странице"
    r = validate_scenario(text)
    assert r["valid"] is False
    assert any("логина" in e for e in r["errors"])


def test_dispatcher_parses_actions():
    d = Dispatcher()
    steps = d.parse('Нажми на кнопку "Войти", введи в поле логин test@test.com, нажми "Отправить", проверь, что появилось сообщение "Добро пожаловать"')
    actions = [s["action"] for s in steps]
    assert "click" in actions
    assert "fill" in actions
    assert "assert_text" in actions


def test_ssrf_blocks_private_ip():
    assert is_private_host("http://127.0.0.1/") is True
    assert is_private_host("http://192.168.1.1/") is True
    try:
        assert_public_url("http://127.0.0.1/")
        assert False
    except ValueError:
        pass


def test_ssrf_allows_public():
    # public hosts resolve fine (or DNS fails -> not private)
    assert is_private_host("https://example.com/") is False