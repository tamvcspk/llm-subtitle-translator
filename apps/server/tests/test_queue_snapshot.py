"""P12b: the queue snapshot says which series and episode a job is for, so Activity need not read file paths."""

from tests.queue_harness import SchedulerCase


class SnapshotNamesTests(SchedulerCase):
    def test_a_job_of_an_episode_a_movie_and_an_older_queue_file_is_named(self):
        series = self.store.upsert_series("arcane", title="Arcane")
        episode = self.store.upsert_unit(series_id=series, season=1, episode=4, title="Oil and Water")
        movie = self.store.upsert_unit(movie_key="heat", title="Heat")
        self.add("ep", unit_id=episode)
        self.add("film", unit_id=movie)
        self.add("old")  # no unit_id: an older queue file settled at start-up
        self.add("gone", unit_id=9999)  # a unit that is no longer there

        jobs = {job["job_id"]: job for job in self.queue.snapshot()["pending"]}
        self.assertEqual((jobs["ep"]["title"], jobs["ep"]["label"]), ("Arcane", "S01E04"))
        self.assertEqual((jobs["film"]["title"], jobs["film"]["label"]), ("Heat", ""))
        self.assertEqual((jobs["old"]["title"], jobs["old"]["label"]), ("", ""))
        self.assertEqual((jobs["gone"]["title"], jobs["gone"]["label"]), ("", ""))

    def test_the_names_of_a_whole_list_cost_one_query_however_many_jobs_and_units(self):
        series = self.store.upsert_series("arcane", title="Arcane")
        for number in range(1, 7):
            unit = self.store.upsert_unit(series_id=series, season=1, episode=number, title=f"E{number}")
            self.add(f"job{number}", unit_id=unit)
            self.add(f"twin{number}", unit_id=unit)  # two jobs of one unit
        seen = []
        real = self.store.query
        self.store.query = lambda sql, args=(): (seen.append(sql), real(sql, args))[1]
        try:
            jobs = self.queue.snapshot()["pending"]
        finally:
            self.store.query = real
        self.assertEqual(len(jobs), 12)
        self.assertEqual(len([sql for sql in seen if "FROM units" in sql]), 1)
        self.assertEqual({job["title"] for job in jobs}, {"Arcane"})

    def test_a_cancelled_job_is_listed_and_counted_on_its_own(self):
        series = self.store.upsert_series("arcane", title="Arcane")
        episode = self.store.upsert_unit(series_id=series, season=1, episode=4, title="x")
        self.add("ep", unit_id=episode)
        self.add("other", unit_id=episode)
        self.assertEqual(self.queue.jobs.request_stop("ep"), "cancelled")  # not started: it ends at once
        snapshot = self.queue.snapshot()
        self.assertEqual((snapshot["counts"]["cancelled"], snapshot["counts"]["pending"]), (1, 1))
        self.assertEqual([(j["job_id"], j["label"]) for j in snapshot["cancelled"]], [("ep", "S01E04")])
        self.assertEqual([j["job_id"] for j in snapshot["pending"]], ["other"])

    def test_the_names_are_read_from_the_job_not_stored_in_it(self):
        series = self.store.upsert_series("arcane", title="Arcane")
        episode = self.store.upsert_unit(series_id=series, season=2, episode=1, title="x")
        self.add("ep", unit_id=episode)
        self.queue.snapshot()
        self.assertNotIn("label", self.queue.jobs.get("ep"))


if __name__ == "__main__":
    import unittest

    unittest.main()
