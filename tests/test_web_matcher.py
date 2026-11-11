from app.services.web_monitor.matcher import (
    find_web_match,
    generate_name_variants,
    matches_web_watch,
)


def test_full_name_and_russian_cases():
    variants = generate_name_variants("Иван Иванов")
    assert "иван иванов" in variants
    assert matches_web_watch("Ивана Иванова пригласили на форум", "Иван Иванов")
    assert matches_web_watch("Иваном Ивановым заинтересовались СМИ", "Иван Иванов")
    assert matches_web_watch("Иване Иванове рассказали отдельно", "Иван Иванов")


def test_does_not_match_single_word_or_substring():
    assert not matches_web_watch("Иванов выступил", "Иван Иванов")
    assert not matches_web_watch("Иван выступил", "Иван Иванов")
    assert not matches_web_watch("ПсевдоИван Иванович", "Иван Иванов")


def test_male_and_female_forms_stay_aligned():
    male = generate_name_variants("Иван Иванов")
    assert "ивана иванова" in male
    assert "иван иванова" not in male
    assert matches_web_watch("Иваном Ивановым сообщили", "Иван Иванов")

    female = generate_name_variants("Анна Иванова")
    assert "анны ивановой" in female
    assert "анну иванову" in female
    assert "анна иванову" not in female
    assert matches_web_watch("Анной Ивановой сообщили", "Анна Иванова")


def test_masculine_surname_cases():
    variants = generate_name_variants("Иван Петровский")

    assert "иван петровский" in variants
    assert "ивана петровского" in variants
    assert "ивану петровскому" in variants
    assert "иваном петровским" in variants
    assert "иване петровском" in variants
    assert matches_web_watch("Иваном Петровским заинтересовались СМИ", "Иван Петровский")


def test_common_male_and_female_names_have_aligned_cases():
    expected = {
        "Иван Иванов": {
            "иван иванов",
            "ивана иванова",
            "ивану иванову",
            "иваном ивановым",
            "иване иванове",
        },
        "Сергей Сидоров": {
            "сергей сидоров",
            "сергея сидорова",
            "сергею сидорову",
            "сергеем сидоровым",
            "сергее сидорове",
        },
        "Дмитрий Волков": {
            "дмитрий волков",
            "дмитрия волкова",
            "дмитрию волкову",
            "дмитрием волковым",
            "дмитрии волкове",
        },
        "Анна Иванова": {
            "анна иванова",
            "анны ивановой",
            "анне ивановой",
            "анну иванову",
            "анной ивановой",
        },
        "Мария Петровская": {
            "мария петровская",
            "марии петровской",
            "марию петровскую",
            "марией петровской",
        },
        "Алексей Кузнецов": {
            "алексей кузнецов",
            "алексея кузнецова",
            "алексею кузнецову",
            "алексеем кузнецовым",
            "алексее кузнецове",
        },
    }
    for query, forms in expected.items():
        variants = set(generate_name_variants(query))
        assert forms <= variants
        for form in forms:
            assert matches_web_watch(form, query)


def test_name_case_forms_do_not_mix_first_name_and_surname_cases():
    variants = generate_name_variants("Иван Петровский")

    assert "иван петровского" not in variants
    assert "ивана петровский" not in variants
    assert "ивану петровским" not in variants


def test_clarification_is_required_when_configured():
    assert find_web_match("Иван Иванов выступил в Москве", "Иван Иванов", "Москва")
    assert find_web_match("Иван Иванов выступил в Казани", "Иван Иванов", "Москва") is None
