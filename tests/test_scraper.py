import unittest

from job_tracker.models import JobPosting
from job_tracker.scraper import (
    _is_job_closed,
    _parse_application_deadline,
    _parse_contract_type,
    _parse_grade_level,
    _parse_remote_status,
    _parse_recruitment_scope,
    posting_matches_filters,
    title_matches_keywords,
)


class TitleKeywordMatchingTests(unittest.TestCase):
    def test_title_matches_expected_keywords(self) -> None:
        self.assertTrue(title_matches_keywords("Senior Data Analyst"))
        self.assertTrue(title_matches_keywords("MEAL Coordinator"))
        self.assertTrue(title_matches_keywords("Artificial Intelligence Advisor"))
        self.assertTrue(title_matches_keywords("M&E Officer"))
        self.assertTrue(title_matches_keywords("IM Specialist"))

    def test_title_does_not_match_unrelated_titles(self) -> None:
        self.assertFalse(title_matches_keywords("Finance Officer"))
        self.assertFalse(title_matches_keywords("Human Resources Manager"))
        self.assertFalse(title_matches_keywords("Medical Doctor"))

    def test_extracts_contract_grade_and_scope(self) -> None:
        self.assertEqual(
            _parse_contract_type(
                "Innovation Analyst UNDP Addis Ababa NO-B, National Professional Officer - Locally recruited position"
            ),
            "National Professional Officer",
        )
        self.assertEqual(_parse_grade_level("NO-B, National Professional Officer - Locally recruited position"), "NO-B")
        self.assertEqual(_parse_grade_level("IPSA 10 - International Personnel Services Agreement"), "IPSA10")
        self.assertEqual(
            _parse_recruitment_scope("UNDP Addis Ababa National NO-B, National Professional Officer - Locally recruited position"),
            "National",
        )
        self.assertEqual(
            _parse_remote_status(
                {"jobLocationType": "TELECOMMUTE"},
                None,
                None,
                "<html></html>",
            ),
            "Remote",
        )
        self.assertEqual(
            _parse_remote_status(
                None,
                "This consultancy is home-based and open globally",
                None,
                "<html></html>",
            ),
            "Remote",
        )
        html = """
        <html><body>
        <h1>Data Scientist</h1>
        <div>Application deadline: March 27, 2026 (6 days)</div>
        </body></html>
        """
        self.assertEqual(_parse_application_deadline(html), "March 27, 2026 (6 days)")
        self.assertFalse(_is_job_closed(html))
        closed_html = "<html><body><h1>Role</h1><div>Closed</div></body></html>"
        self.assertTrue(_is_job_closed(closed_html))

    def test_national_scope_requires_ankara(self) -> None:
        self.assertFalse(
            posting_matches_filters(
                JobPosting(
                    job_id="1",
                    title="Data Analyst",
                    url="https://example.com/1",
                    recruitment_scope="National",
                    location="Addis Ababa",
                )
            )
        )
        self.assertTrue(
            posting_matches_filters(
                JobPosting(
                    job_id="2",
                    title="Data Analyst",
                    url="https://example.com/2",
                    recruitment_scope="National",
                    location="Ankara, Turkey",
                )
            )
        )
        self.assertTrue(
            posting_matches_filters(
                JobPosting(
                    job_id="3",
                    title="Data Analyst",
                    url="https://example.com/3",
                    recruitment_scope="International",
                    location="Addis Ababa",
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
