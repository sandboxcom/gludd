from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/admin/quantization")
    async def admin_quantization_list() -> dict[str, Any]:
        from general_ludd.models.quantization import QuantizationTracker

        tracker: QuantizationTracker | None = getattr(app.state, "_quantization_tracker", None)
        if tracker is None:
            return {"models": []}
        return {"models": tracker.to_dict()}

    @app.post("/admin/quantization/detect")
    async def admin_quantization_detect(req: dict[str, Any]) -> dict[str, Any]:
        import time as _time

        from general_ludd.models.quantization import (
            FireworksDetector,
            HuggingFaceDetector,
            OpenRouterEndpointDetector,
            QuantizationTracker,
            SelfProbeDetector,
        )

        model_id = req.get("model_id", "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if not hasattr(app.state, "_quantization_tracker") or app.state._quantization_tracker is None:
            app.state._quantization_tracker = QuantizationTracker()
        tracker: QuantizationTracker = app.state._quantization_tracker
        fireworks_key = getattr(app.state, "_fireworks_api_key", None)
        hf_detector = HuggingFaceDetector()
        fw_detector = FireworksDetector(api_key=fireworks_key)
        or_detector = OpenRouterEndpointDetector()
        probe_detector = SelfProbeDetector()
        results: list[dict[str, Any]] = []
        for info in await hf_detector.detect(model_id):
            drift = tracker.check_drift(model_id, info)
            tracker.update(model_id, info)
            results.append({
                "precision": info.precision,
                "source": info.source,
                "confidence": info.confidence,
                "provider_name": info.provider_name,
                "drift_detected": drift,
            })
        for info in await fw_detector.detect(model_id):
            drift = tracker.check_drift(model_id, info)
            tracker.update(model_id, info)
            results.append({
                "precision": info.precision,
                "source": info.source,
                "confidence": info.confidence,
                "provider_name": info.provider_name,
                "drift_detected": drift,
            })
        for info in await or_detector.detect(model_id):
            drift = tracker.check_drift(model_id, info)
            tracker.update(model_id, info)
            results.append({
                "precision": info.precision,
                "source": info.source,
                "confidence": info.confidence,
                "provider_name": info.provider_name,
                "drift_detected": drift,
            })
        best = tracker.get(model_id)
        probe_prompt = probe_detector.arithmetic_probe_prompt()
        return {
            "model_id": model_id,
            "sources_checked": len(results),
            "results": results,
            "best": {
                "precision": best.precision,
                "source": best.source,
                "confidence": best.confidence,
            } if best else None,
            "self_probe_prompt": probe_prompt,
            "checked_at": _time.time(),
        }

    @app.get("/admin/quantization/{model_id}")
    async def admin_quantization_get(model_id: str) -> dict[str, Any]:
        from general_ludd.models.quantization import QuantizationTracker

        tracker: QuantizationTracker | None = getattr(app.state, "_quantization_tracker", None)
        if tracker is None:
            return {"model_id": model_id, "precision": None}
        info = tracker.get(model_id)
        if info is None:
            return {"model_id": model_id, "precision": None}
        return {
            "model_id": model_id,
            "precision": info.precision,
            "source": info.source,
            "confidence": info.confidence,
            "provider_name": info.provider_name,
            "bits_estimate": info.bits_estimate,
            "detected_at": info.detected_at,
        }

    @app.post("/admin/quantization/drift-check")
    async def admin_quantization_drift_check() -> dict[str, Any]:
        from general_ludd.models.quantization import (
            HuggingFaceDetector,
            OpenRouterEndpointDetector,
            QuantizationTracker,
        )

        tracker: QuantizationTracker | None = getattr(app.state, "_quantization_tracker", None)
        if tracker is None:
            return {"drift_detected": False, "changes": []}
        hf_detector = HuggingFaceDetector()
        or_detector = OpenRouterEndpointDetector()
        changes: list[dict[str, Any]] = []
        for model_id in list(tracker.list_all().keys()):
            new_infos = await hf_detector.detect(model_id)
            new_infos.extend(await or_detector.detect(model_id))
            for info in new_infos:
                if tracker.check_drift(model_id, info):
                    old_info = tracker.get(model_id)
                    changes.append({
                        "model_id": model_id,
                        "old_precision": old_info.precision if old_info is not None else None,
                        "new_precision": info.precision,
                        "source": info.source,
                    })
                    tracker.update(model_id, info)
        return {
            "drift_detected": len(changes) > 0,
            "changes": changes,
            "models_checked": len(tracker.list_all()),
        }
