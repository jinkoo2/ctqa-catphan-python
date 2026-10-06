from ctqa_catphan.app_settings import DEFAULT_CASE_FOLDER_REGEX, watcher_case_folder_regex
from ctqa_catphan.watcher import case_folder_matches


def test_case_folder_matches_mmddyyyy_dailyqa():
    assert case_folder_matches("10022026_DailyQA", DEFAULT_CASE_FOLDER_REGEX)
    assert case_folder_matches("01012026_DailyQA", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("DailyQA", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("10022026_dailyqa", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("scratch", DEFAULT_CASE_FOLDER_REGEX)
    assert case_folder_matches("anything", "")


def test_watcher_case_folder_regex_default_and_empty():
    assert watcher_case_folder_regex({}) == DEFAULT_CASE_FOLDER_REGEX
    assert watcher_case_folder_regex({"Watcher": {}}) == DEFAULT_CASE_FOLDER_REGEX
    assert watcher_case_folder_regex({"Watcher": {"CASE_FOLDER_NAME_REGEX": ""}}) == ""
    assert (
        watcher_case_folder_regex({"Watcher": {"CASE_FOLDER_NAME_REGEX": r"^\d{8}_DailyQA$"}})
        == r"^\d{8}_DailyQA$"
    )
    assert watcher_case_folder_regex({"case_folder_name_regex": r"^x$"}) == r"^x$"
