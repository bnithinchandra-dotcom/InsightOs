import hashlib
import json
import math
import unittest
from datetime import datetime

import numpy as np

import app.ml_forecasting as forecasting
from app.ml_forecasting import (
    MAX_BACKTEST_FOLDS,
    ForecastError,
    load_model_artifact,
    predict_monthly_forecast,
    train_monthly_forecast,
)


class MonthlyForecastingTests(unittest.TestCase):
    def setUp(self):
        self.dates = [
            datetime(2020 + index // 12, index % 12 + 1, 15).isoformat()
            for index in range(36)
        ]
        self.targets = [
            100 + 4 * index + 18 * math.sin(2 * math.pi * index / 12)
            for index in range(36)
        ]
        self.kwargs = {
            "dates": self.dates,
            "targets": self.targets,
            "date_column": "order_date",
            "target_column": "net_sales",
            "dataset_id": 41,
            "file_id": 9,
            "source_fingerprint": "a" * 64,
            "horizon": 3,
            "artifact_id": "forecast-test-v1",
        }

    def test_trains_real_model_evaluates_chronologically_and_predicts_future(self):
        result = train_monthly_forecast(**self.kwargs)
        artifact = result["artifact"]
        forecast = result["forecast"]

        self.assertEqual(artifact["algorithm"], "sklearn_linear_trend_seasonal")
        self.assertEqual(artifact["model_id"], "forecast-test-v1")
        self.assertEqual(artifact["source"]["fingerprint"], "a" * 64)
        evaluation = artifact["validation"]
        self.assertEqual(evaluation["training_periods"][0], "2020-01")
        self.assertLess(
            evaluation["training_periods"][-1],
            evaluation["periods"][0],
        )
        self.assertEqual(
            evaluation["sample_count"],
            len(evaluation["observed"]),
        )
        self.assertEqual(
            evaluation["candidate"],
            self._metrics(
                evaluation["observed"],
                evaluation["candidate_predictions"],
            ),
        )
        self.assertEqual(
            evaluation["baseline"],
            self._metrics(
                evaluation["observed"],
                evaluation["baseline_predictions"],
            ),
        )
        self.assertEqual(
            [item["period"] for item in forecast["forecast"]],
            ["2023-01", "2023-02", "2023-03"],
        )
        self.assertTrue(all(math.isfinite(item["predicted"]) for item in forecast["forecast"]))
        self.assertEqual(forecast["source"]["fingerprint"], "a" * 64)
        self.assertEqual(len(result["artifact_sha256"]), 64)
        self.assertEqual(
            bool(forecast["warnings"]),
            evaluation["comparison"]["outcome"] != "beats_baseline",
        )
        self.assertEqual(evaluation["method"], "chronological_holdout")
        self.assertEqual(evaluation["period_start"], evaluation["periods"][0])
        self.assertEqual(evaluation["period_end"], evaluation["periods"][-1])

    def test_saved_artifact_round_trips_and_produces_same_forecast(self):
        result = train_monthly_forecast(**self.kwargs)
        artifact_bytes = result["artifact_bytes"]
        artifact = load_model_artifact(
            artifact_bytes,
            hashlib.sha256(artifact_bytes).hexdigest(),
        )
        forecast = predict_monthly_forecast(
            artifact=artifact,
            history=[
                sum(self.kwargs["targets"][index : index + 1])
                for index in range(len(self.kwargs["targets"]))
            ],
            history_periods=self._periods(36),
            horizon=3,
            dataset_id=41,
            file_id=9,
            source_fingerprint="a" * 64,
            artifact_sha256=result["artifact_sha256"],
        )
        self.assertEqual(forecast["forecast"], result["forecast"]["forecast"])
        self.assertEqual(forecast["artifact_sha256"], result["artifact_sha256"])

    def test_prediction_rejects_stale_source_versions(self):
        result = train_monthly_forecast(**self.kwargs)
        with self.assertRaises(ForecastError) as raised:
            predict_monthly_forecast(
                artifact=result["artifact"],
                history=self.targets,
                history_periods=self._periods(36),
                horizon=3,
                dataset_id=41,
                file_id=9,
                source_fingerprint="b" * 64,
            )
        self.assertEqual(raised.exception.code, "model_source_mismatch")

    def test_duplicate_rows_are_aggregated_as_monthly_sums(self):
        result = train_monthly_forecast(
            **{
                **self.kwargs,
                "dates": self.dates + [self.dates[0]],
                "targets": self.targets + [5.0],
            }
        )
        first_observation = result["forecast"]["historical"][0]
        self.assertEqual(first_observation["period"], "2020-01")
        self.assertEqual(first_observation["observed"], self.targets[0] + 5.0)

    def test_candidate_and_baseline_use_same_held_out_periods(self):
        result = train_monthly_forecast(**self.kwargs)
        validation = result["artifact"]["validation"]
        self.assertEqual(
            len(validation["periods"]),
            len(validation["candidate_predictions"]),
        )
        self.assertEqual(
            validation["periods"],
            self._periods(36)[len(validation["training_periods"]) :],
        )
        self.assertEqual(
            len(validation["candidate_predictions"]),
            len(validation["baseline_predictions"]),
        )

    def test_zero_targets_return_no_divide_by_zero_percentage_metric(self):
        result = train_monthly_forecast(
            **{
                **self.kwargs,
                "targets": [0.0] * len(self.kwargs["targets"]),
            }
        )
        validation = result["artifact"]["validation"]
        self.assertIsNone(validation["candidate"]["wape"])
        self.assertIsNone(validation["baseline"]["wape"])

    def test_invalid_or_insufficient_data_is_rejected(self):
        cases = (
            (
                {**self.kwargs, "dates": ["not-a-date"] * len(self.kwargs["dates"])},
                "invalid_dates",
            ),
            (
                {**self.kwargs, "targets": [None] * len(self.kwargs["targets"])},
                "invalid_targets",
            ),
            (
                {**self.kwargs, "targets": ["bad"] * len(self.kwargs["targets"])},
                "invalid_targets",
            ),
            (
                {**self.kwargs, "dates": self.kwargs["dates"][:10], "targets": self.kwargs["targets"][:10]},
                "insufficient_history",
            ),
        )
        for arguments, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ForecastError) as raised:
                    train_monthly_forecast(**arguments)
                self.assertEqual(raised.exception.code, expected_code)

    def test_missing_months_are_not_silently_imputed(self):
        arguments = {
            **self.kwargs,
            "dates": self.dates[:18] + self.dates[19:],
            "targets": self.targets[:18] + self.targets[19:],
        }
        with self.assertRaises(ForecastError) as raised:
            train_monthly_forecast(**arguments)
        self.assertEqual(raised.exception.code, "missing_periods")

    def test_corrupt_and_incompatible_artifacts_are_rejected(self):
        result = train_monthly_forecast(**self.kwargs)
        encoded = result["artifact_bytes"]
        with self.assertRaises(ForecastError) as raised:
            load_model_artifact(encoded, "0" * 64)
        self.assertEqual(raised.exception.code, "model_artifact_integrity_failed")
        with self.assertRaises(ForecastError) as raised:
            predict_monthly_forecast(
                artifact={"algorithm": "user-supplied-path"},
                history=self.targets,
                history_periods=self._periods(36),
                horizon=3,
                dataset_id=41,
                file_id=9,
                source_fingerprint="a" * 64,
            )
        self.assertEqual(raised.exception.code, "model_artifact_invalid")

    @staticmethod
    def _metrics(actual, predicted):
        actual = np.asarray(actual, dtype=float)
        predicted = np.asarray(predicted, dtype=float)
        errors = predicted - actual
        absolute_errors = np.abs(errors)
        denominator = np.sum(np.abs(actual))
        return {
            "mae": float(np.mean(absolute_errors)),
            "rmse": float(np.sqrt(np.mean(np.square(errors)))),
            "wape": (
                float(np.sum(absolute_errors) / denominator * 100)
                if denominator > forecasting.WAPE_NEAR_ZERO_TOLERANCE
                else None
            ),
            "wape_status": (
                "available"
                if denominator > forecasting.WAPE_NEAR_ZERO_TOLERANCE
                else "unavailable"
            ),
            "wape_reason": (
                None
                if denominator > forecasting.WAPE_NEAR_ZERO_TOLERANCE
                else "The sum of absolute actual values is zero or below 1e-12."
            ),
        }

    def test_expanding_backtest_is_bounded_chronological_and_uses_past_only(self):
        baseline = train_monthly_forecast(**self.kwargs)
        validation = baseline["artifact"]["validation"]
        backtesting = validation["backtesting"]
        folds = backtesting["folds"]

        self.assertEqual(backtesting["method"], "expanding_window")
        self.assertLessEqual(len(folds), MAX_BACKTEST_FOLDS)
        self.assertEqual(backtesting["fold_count"], len(folds))
        for fold in folds:
            self.assertLess(fold["training_end"], fold["evaluation_start"])
            self.assertLessEqual(fold["evaluation_start"], fold["evaluation_end"])
            self.assertEqual(
                fold["training_observations"],
                self._periods(36).index(fold["training_end"]) + 1,
            )
        self.assertEqual(
            set(validation["baselines"]),
            {"last_observation", "seasonal_naive_12"},
        )
        self.assertEqual(validation["baseline_name"], "seasonal_naive_12")
        self.assertEqual(
            validation["comparison"]["baseline_name"],
            validation["baseline_name"],
        )

        changed_future = [
            value + 100_000 if index >= 25 else value
            for index, value in enumerate(self.targets)
        ]
        changed = train_monthly_forecast(
            **{**self.kwargs, "targets": changed_future}
        )
        self.assertEqual(
            changed["artifact"]["validation"]["backtesting"]["folds"][0][
                "candidate_mae"
            ],
            folds[0]["candidate_mae"],
        )
        self.assertEqual(
            changed["artifact"]["validation"]["backtesting"]["folds"][0][
                "last_observation_mae"
            ],
            folds[0]["last_observation_mae"],
        )

    def test_uncertainty_uses_out_of_sample_errors_and_has_finite_ordered_bounds(self):
        result = train_monthly_forecast(**self.kwargs)
        forecast = result["forecast"]
        uncertainty = forecast["uncertainty"]

        self.assertEqual(uncertainty["status"], "available")
        self.assertEqual(
            uncertainty["method"],
            "expanding_window_empirical_absolute_error_envelope",
        )
        self.assertTrue(
            all(item["sample_count"] == uncertainty["available_folds"]
                for item in uncertainty["lead_time_bounds"])
        )
        for item in forecast["forecast"]:
            self.assertTrue(math.isfinite(item["lower_bound"]))
            self.assertTrue(math.isfinite(item["upper_bound"]))
            self.assertLessEqual(item["lower_bound"], item["predicted"])
            self.assertLessEqual(item["predicted"], item["upper_bound"])
        json.dumps(forecast, allow_nan=False)

    def test_uncertainty_is_unavailable_for_long_horizon_with_too_few_origins(self):
        result = train_monthly_forecast(
            **{**self.kwargs, "horizon": 24}
        )
        uncertainty = result["forecast"]["uncertainty"]

        self.assertEqual(uncertainty["status"], "unavailable")
        self.assertLess(uncertainty["available_folds"], uncertainty["required_folds"])
        self.assertTrue(all(
            item["lower_bound"] is None and item["upper_bound"] is None
            for item in result["forecast"]["forecast"]
        ))
        self.assertIn(
            "long_horizon_relative_to_history",
            [
                finding["code"]
                for finding in result["forecast"]["suitability"]["findings"]
            ],
        )

    def test_suitability_reports_constant_and_duplicate_month_observations(self):
        result = train_monthly_forecast(
            **{
                **self.kwargs,
                "dates": self.dates + [self.dates[0]],
                "targets": [10.0] * len(self.targets) + [0.0],
            }
        )
        findings = result["forecast"]["suitability"]["findings"]
        codes = {finding["code"] for finding in findings}

        self.assertIn("constant_or_near_constant_target", codes)
        self.assertIn("duplicate_month_rows_aggregated", codes)
        self.assertEqual(result["forecast"]["suitability"]["status"], "warning")

    def test_blocked_data_errors_include_structured_suitability_findings(self):
        cases = (
            (
                {
                    **self.kwargs,
                    "dates": self.dates[:14],
                    "targets": self.targets[:14],
                },
                "insufficient_history",
            ),
            (
                {
                    **self.kwargs,
                    "dates": self.dates[:18] + self.dates[19:],
                    "targets": self.targets[:18] + self.targets[19:],
                },
                "missing_periods",
            ),
            (
                {
                    **self.kwargs,
                    "dates": ["not-a-date"] * len(self.dates),
                },
                "invalid_dates",
            ),
        )
        for arguments, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ForecastError) as raised:
                    train_monthly_forecast(**arguments)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(raised.exception.findings[0]["severity"], "blocked")
                self.assertEqual(raised.exception.findings[0]["code"], expected_code)

    def test_wape_near_zero_denominator_is_explicitly_unavailable(self):
        metrics = forecasting._metrics(
            np.asarray([1e-15, -1e-15]),
            np.asarray([2e-15, -2e-15]),
        )

        self.assertIsNone(metrics["wape"])
        self.assertEqual(metrics["wape_status"], "unavailable")
        self.assertTrue(metrics["wape_reason"])

    def test_baseline_comparison_uses_documented_tolerance(self):
        comparison = forecasting._baseline_comparison(
            {"mae": 9.95},
            {"mae": 10.0},
            "last_observation",
        )

        self.assertEqual(comparison["outcome"], "within_tolerance")
        self.assertEqual(comparison["mae_tolerance"], 0.1)
        self.assertEqual(comparison["tolerance_relative"], 0.01)

    @staticmethod
    def _periods(count):
        return [
            f"{2020 + index // 12:04d}-{index % 12 + 1:02d}"
            for index in range(count)
        ]


if __name__ == "__main__":
    unittest.main()
