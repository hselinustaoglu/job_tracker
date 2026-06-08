from datetime import date
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from job_tracker.models import JobPosting
from job_tracker.scraper import (
    _AbIlanListParser,
    _build_eeas_posting,
    _build_ab_ilan_posting,
    _build_reliefweb_posting,
    _extract_ab_ilan_fields,
    _ReliefWebListParser,
    _is_job_closed,
    _is_past_deadline,
    _is_turkiye_eeas_job,
    _parse_application_deadline,
    _parse_contract_type,
    _parse_grade_level,
    _parse_remote_status,
    _parse_recruitment_scope,
    fetch_matching_jobs,
    posting_matches_filters,
    title_has_excluded_terms,
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

    def test_fetch_matching_jobs_skips_failed_search_pages(self) -> None:
        error = HTTPError(
            url="https://www.impactpool.org/search",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with (
            patch("job_tracker.scraper._build_search_specs", return_value=["https://www.impactpool.org/search"]),
            patch("job_tracker.scraper._fetch_text", side_effect=error),
            patch("job_tracker.scraper.fetch_eeas_jobs", return_value=[]),
            patch("job_tracker.scraper.fetch_ab_ilan_jobs", return_value=[]),
            patch("job_tracker.scraper.fetch_reliefweb_jobs", return_value=[]),
        ):
            self.assertEqual(fetch_matching_jobs(), [])

    def test_eeas_turkiye_filter_and_expired_exclusion(self) -> None:
        self.assertTrue(
            _is_turkiye_eeas_job(
                title="Vacancy - Policy Assistant",
                location="Ankara",
                teaser="The Delegation of the European Union to Türkiye is recruiting.",
            )
        )
        self.assertFalse(
            _is_turkiye_eeas_job(
                title="Vacancy - Policy Assistant",
                location="Brussels",
                teaser="EEAS headquarters role.",
            )
        )
        posting = _build_eeas_posting(
            {
                "title": "Vacancy - Data Analyst",
                "url": "/eeas/vacancy-data-analyst_en",
                "details": "New opportunity Teaser The Delegation of the European Union to Türkiye seeks support on data analysis. Location Ankara Category Local Agent Deadline 15.04.2026",
            }
        )
        self.assertIsNotNone(posting)
        assert posting is not None
        self.assertEqual(posting.source, "EEAS")
        self.assertEqual(posting.recruitment_scope, "National")
        self.assertEqual(posting.location, "Ankara")
        self.assertEqual(posting.application_deadline, "15.04.2026")
        self.assertTrue(posting_matches_filters(posting))
        policy_posting = _build_eeas_posting(
            {
                "title": "Vacancy - Project Officer",
                "url": "/eeas/vacancy-project-officer_en",
                "details": "New opportunity Teaser The Delegation of the European Union to Türkiye is recruiting. Location Ankara Category Local Agent Deadline 17.04.2026",
            }
        )
        self.assertIsNotNone(policy_posting)
        assert policy_posting is not None
        self.assertTrue(posting_matches_filters(policy_posting))
        self.assertIsNone(
            _build_eeas_posting(
                {
                    "title": "Vacancy - Data Analyst",
                    "url": "/eeas/vacancy-data-analyst_en",
                    "details": "Expired Teaser The Delegation of the European Union to Türkiye seeks support on data analysis. Location Ankara Category Local Agent Deadline 15.04.2026",
                }
            )
        )

    def test_excludes_internship_roles(self) -> None:
        self.assertTrue(title_has_excluded_terms("Data Science Intern"))
        self.assertTrue(title_has_excluded_terms("AI Internship"))
        self.assertFalse(title_has_excluded_terms("International Data Analyst"))
        self.assertFalse(
            posting_matches_filters(
                JobPosting(
                    job_id="4",
                    title="Data Science Intern",
                    url="https://example.com/4",
                    recruitment_scope="International",
                    location="Ankara",
                )
            )
        )
        self.assertTrue(
            posting_matches_filters(
                JobPosting(
                    job_id="6",
                    title="International Data Analyst",
                    url="https://example.com/6",
                    recruitment_scope="International",
                    location="Remote",
                )
            )
        )
        self.assertFalse(
            posting_matches_filters(
                JobPosting(
                    job_id="5",
                    title="AI Internship",
                    url="https://example.com/5",
                    recruitment_scope="International",
                    location="Remote",
                )
            )
        )

    def test_extracts_ab_ilan_labeled_fields(self) -> None:
        fields = _extract_ab_ilan_fields(
            "İlan Başlığı : Project Officer Lokasyon : Ankara Son Başvuru Tarihi : 15.04.2026"
        )
        self.assertEqual(fields["job_title"], "Project Officer")
        self.assertEqual(fields["location"], "Ankara")
        self.assertEqual(fields["deadline"], "15.04.2026")

    def test_ab_ilan_filters_location_and_deadline(self) -> None:
        posting = _build_ab_ilan_posting(
            {
                "title": "MEAL Officer",
                "url": "/is-ilani/meal-officer/",
                "organization": "Example NGO",
                "location": "Uzaktan",
                "deadline": "30/03/2030",
            }
        )
        self.assertIsNotNone(posting)
        assert posting is not None
        self.assertTrue(posting_matches_filters(posting))
        self.assertFalse(_is_past_deadline("27/03/2026", today=date(2026, 3, 27)))
        self.assertTrue(_is_past_deadline("27/03/2026", today=date(2026, 3, 28)))
        self.assertFalse(
            posting_matches_filters(
                JobPosting(
                    job_id="ab-ilan:test",
                    title="Data Analyst",
                    url="https://example.com",
                    location="Istanbul",
                    application_deadline="30/03/2030",
                    source="AB-ilan",
                )
            )
        )

    def test_ab_ilan_list_row_parser(self) -> None:
        parser = _AbIlanListParser()
        parser.feed(
            """
            <table>
              <tr><th>No</th><th>Tarih</th><th>İlan Başlığı</th><th>Kuruluş</th><th>Lokasyon</th><th>Son Başvuru Tarihi</th></tr>
              <tr>
                <td>1</td>
                <td>28/03/2026</td>
                <td><a href="/is-ilani/project-officer/">Project Officer</a></td>
                <td>Example NGO</td>
                <td>Ankara</td>
                <td>31/03/2026</td>
              </tr>
            </table>
            """
        )
        parser.close()
        self.assertEqual(len(parser.rows), 1)
        self.assertEqual(parser.rows[0]["title"], "Project Officer")
        self.assertEqual(parser.rows[0]["location"], "Ankara")
        self.assertEqual(parser.rows[0]["deadline"], "31/03/2026")

    def test_reliefweb_list_parser_and_filters(self) -> None:
        parser = _ReliefWebListParser()
        parser.feed(
            """
            <article class="rw-river-article--card rw-river-article rw-river-article--job" data-id="4204802">
              <header><h3 class="rw-river-article__title"><a href="https://reliefweb.int/job/4204802/test-role">Remote Data Analyst</a></h3></header>
              <footer>
                <dl class="rw-meta rw-article-meta rw-entity-meta rw-entity-meta--core">
                  <dt class="rw-entity-meta__tag-label rw-entity-meta__tag-label--source">Organization</dt>
                  <dd class="rw-entity-meta__tag-value rw-entity-meta__tag-value--source">Example Org</dd>
                  <dt class="rw-entity-meta__tag-label rw-entity-meta__tag-label--posted">Posted</dt>
                  <dd class="rw-entity-meta__tag-value rw-entity-meta__tag-value--posted"><time>27 Mar 2026</time></dd>
                  <dt class="rw-entity-meta__tag-label rw-entity-meta__tag-label--closing-date">Closing date</dt>
                  <dd class="rw-entity-meta__tag-value rw-entity-meta__tag-value--closing-date"><time>30 Apr 2030</time></dd>
                </dl>
              </footer>
            </article>
            """
        )
        parser.close()
        self.assertEqual(len(parser.items), 1)
        posting = _build_reliefweb_posting(parser.items[0])
        self.assertIsNotNone(posting)
        assert posting is not None
        self.assertEqual(posting.organization, "Example Org")
        self.assertEqual(posting.application_deadline, "30 Apr 2030")
        self.assertEqual(posting.remote_status, "Remote")
        self.assertTrue(posting_matches_filters(posting))
        expired = _build_reliefweb_posting(
            {
                "title": "Remote Data Analyst",
                "url": "https://reliefweb.int/job/test",
                "organization": "Example Org",
                "posted_date": "27 Mar 2026",
                "closing_date": "27 Mar 2026",
            }
        )
        self.assertIsNotNone(expired)
        assert expired is not None
        self.assertTrue(_is_past_deadline(expired.application_deadline, today=date(2026, 3, 28)))
        self.assertFalse(
            posting_matches_filters(
                JobPosting(
                    job_id="reliefweb:test2",
                    title="Remote AI Internship",
                    url="https://reliefweb.int/job/test2",
                    application_deadline="30 Apr 2030",
                    source="ReliefWeb",
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
