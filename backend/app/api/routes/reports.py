"""7天复盘 API"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.predictions import prediction_precedes_publication
from app.db.session import get_db
from app.models.content_prediction import ContentPrediction
from app.models.draft import Draft
from app.models.metric import Metric
from app.models.publish_log import PublishLog
from app.models.topic import Topic
from app.models.weekly_report import WeeklyReport
from app.schemas.weekly_report import WeeklyReportCreate, WeeklyReportOut

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/weekly", response_model=list[WeeklyReportOut])
def list_reports(db: Session = Depends(get_db)):
    reports = db.query(WeeklyReport).order_by(WeeklyReport.created_at.desc()).limit(10).all()
    return [WeeklyReportOut.model_validate(r) for r in reports]


@router.get("/weekly/{report_id}", response_model=WeeklyReportOut)
def get_report(report_id: int, db: Session = Depends(get_db)):
    r = db.query(WeeklyReport).filter(WeeklyReport.id == report_id).first()
    if not r:
        raise HTTPException(404, "复盘报告不存在")
    return WeeklyReportOut.model_validate(r)


@router.post("/weekly", response_model=WeeklyReportOut, status_code=201)
def create_report(body: WeeklyReportCreate, db: Session = Depends(get_db)):
    if body.end_date < body.start_date:
        raise HTTPException(400, "结束日期不能早于开始日期")

    logs = db.query(PublishLog).order_by(PublishLog.created_at.desc()).all()
    rows: list[dict] = []
    published_posts = 0
    missing_metric_log_ids: list[int] = []
    angle_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)
    template_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)
    content_type_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)

    for log in logs:
        log_date = (log.published_at or log.created_at).date()
        if not (body.start_date <= log_date <= body.end_date):
            continue
        published_posts += 1

        draft = db.query(Draft).filter(Draft.id == log.draft_id).first()
        topic = db.query(Topic).filter(Topic.id == draft.topic_id).first() if draft else None
        latest_metric = (
            db.query(Metric)
            .filter(Metric.publish_log_id == log.id)
            .order_by(Metric.collected_at.desc(), Metric.id.desc())
            .first()
        )

        if latest_metric is None:
            missing_metric_log_ids.append(log.id)
            continue
        metric = latest_metric

        rates = _metric_rates(metric)
        prediction = _prediction_for_log(log, db)
        prediction_error = _prediction_error(prediction, metric) if prediction else None
        angle = topic.content_angle if topic and topic.content_angle else "未标注角度"
        template = draft.template_key if draft and draft.template_key else "未标注模板"
        content_type = draft.content_type if draft and draft.content_type else "未标注类型"
        engagement = metric.likes + metric.favorites + metric.comments + metric.shares + metric.new_followers
        row = {
            "log": log,
            "topic": topic,
            "draft": draft,
            "angle": angle,
            "template": template,
            "content_type": content_type,
            "metric": metric,
            "prediction": prediction,
            "prediction_error": prediction_error,
            "engagement": engagement,
            **rates,
        }
        rows.append(row)

        _accumulate_stats(angle_stats, angle, metric)
        _accumulate_stats(template_stats, template, metric)
        _accumulate_stats(content_type_stats, content_type, metric)

    rows.sort(key=lambda item: (item["engagement"], item["metric"].views), reverse=True)

    total_posts = published_posts
    total_views = sum(item["metric"].views for item in rows)
    total_likes = sum(item["metric"].likes for item in rows)
    total_favorites = sum(item["metric"].favorites for item in rows)
    total_comments = sum(item["metric"].comments for item in rows)
    total_shares = sum(item["metric"].shares for item in rows)
    total_followers = sum(item["metric"].new_followers for item in rows)
    total_impressions = sum(item["metric"].impressions or 0 for item in rows)
    total_save_rate = _safe_rate(total_favorites, total_views)
    total_like_rate = _safe_rate(total_likes, total_views)
    total_comment_rate = _safe_rate(total_comments, total_views)
    total_follow_conversion = _safe_rate(
        total_followers,
        total_impressions or total_views,
    )

    best_items = [_topic_snapshot(item) for item in rows[:3]]
    worst_source = rows[-3:] if len(rows) > 1 else []
    worst_items = [_topic_snapshot(item) for item in worst_source]
    angle_performance = _build_group_performance(angle_stats)
    template_performance = _build_group_performance(template_stats)
    content_type_performance = _build_group_performance(content_type_stats)
    prediction_calibration = _build_prediction_calibration(rows)
    prediction_calibration.update({
        "excluded_missing_metrics": len(missing_metric_log_ids),
        "measured_without_baseline_count": sum(item["prediction"] is None for item in rows),
        "baseline_policy": "single_linked_prepublication_prediction",
    })
    recommendations = _build_recommendations(
        rows,
        angle_performance,
        template_performance,
        content_type_performance,
        prediction_calibration,
    )
    if missing_metric_log_ids:
        recommendations.insert(0, f"本周期有 {len(missing_metric_log_ids)} 条发布记录尚未录入指标；请先补齐，缺失记录不参与排名、误差或表现建议。")
    report_text = _build_report_text(
        body.start_date,
        body.end_date,
        total_posts,
        total_views,
        total_likes,
        total_favorites,
        total_comments,
        total_followers,
        total_save_rate,
        total_like_rate,
        total_comment_rate,
        total_follow_conversion,
        best_items,
        recommendations,
        prediction_calibration,
        measured_posts=len(rows),
        missing_metric_posts=len(missing_metric_log_ids),
    )

    report = WeeklyReport(
        start_date=body.start_date,
        end_date=body.end_date,
        report_text=report_text,
        best_topics={"items": best_items},
        worst_topics={"items": worst_items},
        angle_performance={"items": angle_performance},
        template_performance={"items": template_performance},
        content_type_performance={"items": content_type_performance},
        performance_summary={
            "data_coverage": {
                "published_posts": published_posts,
                "measured_posts": len(rows),
                "missing_metric_posts": len(missing_metric_log_ids),
                "missing_metric_log_ids": missing_metric_log_ids,
            },
            "totals": {
                "posts": total_posts,
                "views": total_views,
                "likes": total_likes,
                "favorites": total_favorites,
                "comments": total_comments,
                "shares": total_shares,
                "new_followers": total_followers,
                "engagement": total_likes + total_favorites + total_comments + total_shares + total_followers,
            },
            "rates": {
                "save_rate": total_save_rate,
                "like_rate": total_like_rate,
                "comment_rate": total_comment_rate,
                "follow_conversion_rate": total_follow_conversion,
            } if rows else None,
            "prediction_calibration": prediction_calibration,
        },
        prediction_calibration=prediction_calibration,
        recommendations={"items": recommendations},
    )
    db.add(report)
    for item in rows:
        topic = item["topic"]
        if topic and topic.status == "published":
            topic.status = "reviewed"
    db.commit()
    db.refresh(report)
    return WeeklyReportOut.model_validate(report)


def _topic_snapshot(item: dict) -> dict:
    topic = item["topic"]
    metric = item["metric"]
    draft = item["draft"]
    prediction = item.get("prediction")
    return {
        "topic_id": topic.id if topic else None,
        "title": topic.title if topic else "未关联选题",
        "content_angle": topic.content_angle if topic else "",
        "content_type": draft.content_type if draft else None,
        "template_key": draft.template_key if draft else None,
        "views": metric.views,
        "likes": metric.likes,
        "favorites": metric.favorites,
        "comments": metric.comments,
        "shares": metric.shares,
        "new_followers": metric.new_followers,
        "engagement": item["engagement"],
        "save_rate": item["save_rate"],
        "like_rate": item["like_rate"],
        "comment_rate": item["comment_rate"],
        "follow_conversion_rate": item["follow_conversion_rate"],
        "prediction": _prediction_snapshot(prediction) if prediction else None,
        "prediction_error": item.get("prediction_error"),
    }


def _build_recommendations(
    rows: list[dict],
    angle_performance: list[dict],
    template_performance: list[dict],
    content_type_performance: list[dict],
    prediction_calibration: dict,
) -> list[str]:
    if not rows:
        return [
            "本周期没有已录入的实际指标，暂不提供表现排名或内容方向建议。",
            "发布前先记录预测，再录入真实数据，后续才能校准选题判断。",
            "每条内容发布后记录浏览、点赞、收藏、评论和新增粉丝。",
        ]

    if len(rows) < 3:
        items = [
            f"本周期仅有 {len(rows)} 条已录入指标的记录，不足 3 条，暂不判断内容方向、模板优劣或发布频次。3 条是产品护栏，不代表达到统计显著性。",
            f"当前可观察到合计浏览 {sum(item['metric'].views for item in rows)}、收藏 {sum(item['metric'].favorites for item in rows)}；这些数字仅描述已录入记录。",
            "先补齐发布记录的实际指标和角度、模板、内容类型标签，再按一致的观察时长收集对照数据。",
        ]
        for item in rows:
            prediction = item.get("prediction")
            error = item.get("prediction_error")
            if prediction is not None and error is not None:
                items.append(f"已测记录的预测浏览 {prediction.predicted_views}、实际浏览 {item['metric'].views}，差值 {error['views_error']:+d}；这只是当前样本的观察。")
    else:
        items = [
            f"本周期有 {len(rows)} 条已录入指标的记录。3 条仅是展示分组观察的产品护栏，不代表统计显著性，也不足以决定增减发布频次。",
        ]
        for dimension, groups in (("角度", angle_performance), ("模板", template_performance), ("内容类型", content_type_performance)):
            labelled = [group for group in groups if str(group["label"]).strip() and not str(group["label"]).strip().startswith("未标注")]
            if len(labelled) != len(groups):
                items.append(f"请补齐尚未标注的{dimension}标签；缺少标签的记录不用于该维度的比较。")
            comparable = [group for group in labelled if int(group["views"]) > 0]
            if len({group["label"] for group in comparable}) < 2:
                items.append(f"{dimension}尚无至少两种已标注且有浏览数据的类别，先收集对照样本，暂不判断优劣。")
                continue
            ordered = sorted(comparable, key=lambda group: float(group["save_rate"]), reverse=True)
            high, low = ordered[0], ordered[-1]
            if high["label"] == low["label"] or high["save_rate"] == low["save_rate"]:
                items.append(f"已标注的{dimension}类别当前收藏率相同，先补充对照数据，暂不判断优劣。")
                continue
            items.append(f"已录入样本中，{dimension}“{high['label']}”（{high['posts']} 条）的收藏率为 {high['save_rate'] * 100:.1f}%，“{low['label']}”（{low['posts']} 条）为 {low['save_rate'] * 100:.1f}%；先在相同观察时长下补充对照，不能据此归因或调整频次。")
    if prediction_calibration.get("prediction_count"):
        avg_error = prediction_calibration.get("avg_abs_view_error_rate")
        bias = _bias_label(prediction_calibration.get("view_bias"))
        if avg_error is not None:
            samples = prediction_calibration["relative_error_sample_count"]
            items.append(f"本周期 {samples} 条有效分母样本的平均浏览相对误差约 {float(avg_error) * 100:.1f}%，已记录预测的偏差为“{bias}”；这里只描述当前样本。")
        else:
            items.append(f"本周期预测浏览基数均为零，相对误差不可计算；保留绝对误差，偏差方向为“{bias}”。")
        if prediction_calibration.get("undefined_relative_error_count"):
            items.append(f"{prediction_calibration['undefined_relative_error_count']} 条预测浏览为零的记录已从相对误差平均值分母排除。")
    else:
        items.append("后续请在发布前保存预测，再录入实际指标，积累可以比较的预测差值。")
    return items


def _build_report_text(
    start_date,
    end_date,
    total_posts,
    total_views,
    total_likes,
    total_favorites,
    total_comments,
    total_followers,
    total_save_rate,
    total_like_rate,
    total_comment_rate,
    total_follow_conversion,
    best_items,
    recommendations,
    prediction_calibration,
    *,
    measured_posts,
    missing_metric_posts,
) -> str:
    best_title = best_items[0]["title"] if best_items else "暂无"
    lines = [
        f"{start_date} 至 {end_date} 复盘",
        "",
        f"本周期发布 {total_posts} 条内容，已录入指标 {measured_posts} 条，缺失指标 {missing_metric_posts} 条。缺失指标不记为零，不参与排名或预测校准。",
        (f"已录入内容合计浏览 {total_views}，点赞 {total_likes}，收藏 {total_favorites}，评论 {total_comments}，新增粉丝 {total_followers}。" if measured_posts else "暂无实际指标，不能判断本周期内容表现。"),
        (f"已录入内容收藏率 {total_save_rate * 100:.1f}%，点赞率 {total_like_rate * 100:.1f}%，评论率 {total_comment_rate * 100:.1f}%，关注转化率 {total_follow_conversion * 100:.1f}%。" if measured_posts else "关键率暂无数据。"),
        (f"已录入内容中表现最好的选题：{best_title}。" if best_items else "暂不进行表现排名。"),
        "",
        "统计口径：",
        "1. 每条发布记录取最新一次指标快照；明确录入的零值属于实际数据。",
        "2. 预测校准仅使用唯一关联、在发布前保存且已锁定的基准；事后预测、未关联或存在歧义的历史基准均不参与。",
        "3. 以下建议由规则生成，仅供本周期样本复盘，不代表因果结论。",
    ]
    if prediction_calibration.get("prediction_count"):
        avg_error = prediction_calibration["avg_abs_view_error_rate"]
        error_summary = (
            f"其中 {prediction_calibration['relative_error_sample_count']} 条可计算相对误差，平均约 {avg_error * 100:.1f}%"
            if avg_error is not None else "预测浏览基数均为零，相对误差不可计算"
        )
        lines.extend([
            f"4. 本周期有 {prediction_calibration['prediction_count']} 条内容可做预测校准；{error_summary}。",
            f"预测浏览为零的 {prediction_calibration['undefined_relative_error_count']} 条记录不进入相对误差均值分母，但保留绝对误差和偏差方向。",
            f"5. 预测偏差方向：{_bias_label(prediction_calibration.get('view_bias'))}。",
        ])
    else:
        lines.append("4. 当前没有同时具备有效事前预测和实际指标的可校准记录，后续应先记录预测再发布。")
    lines.extend([
        "",
        "下周建议：",
    ])
    lines.extend([f"- {item}" for item in recommendations])
    return "\n".join(lines)


def _prediction_for_log(log: PublishLog, db: Session) -> ContentPrediction | None:
    linked = (
        db.query(ContentPrediction)
        .filter(
            ContentPrediction.publish_log_id == log.id,
            ContentPrediction.draft_id == log.draft_id,
            ContentPrediction.platform == log.platform,
            ContentPrediction.status.in_(["locked", "settled"]),
        )
        .all()
    )
    eligible = [item for item in linked if prediction_precedes_publication(item, log)]
    # 旧数据出现多份关联基准时不猜测、也不选事后追加的最新一条。
    return eligible[0] if len(eligible) == 1 else None


def _prediction_snapshot(prediction: ContentPrediction) -> dict:
    return {
        "id": prediction.id,
        "draft_id": prediction.draft_id,
        "publish_log_id": prediction.publish_log_id,
        "platform": prediction.platform,
        "predicted_views": prediction.predicted_views,
        "predicted_likes": prediction.predicted_likes,
        "predicted_favorites": prediction.predicted_favorites,
        "predicted_comments": prediction.predicted_comments,
        "predicted_shares": prediction.predicted_shares,
        "predicted_new_followers": prediction.predicted_new_followers,
        "predicted_save_rate": float(prediction.predicted_save_rate or 0),
        "predicted_like_rate": float(prediction.predicted_like_rate or 0),
        "predicted_comment_rate": float(prediction.predicted_comment_rate or 0),
        "predicted_follow_conversion_rate": float(prediction.predicted_follow_conversion_rate or 0),
        "confidence": prediction.confidence,
        "rubric_version": prediction.rubric_version,
        "status": prediction.status,
    }


def _prediction_error(prediction: ContentPrediction, metric: Metric) -> dict:
    rates = _metric_rates(metric)
    views = int(metric.views or 0)
    predicted_views = int(prediction.predicted_views or 0)
    view_error = views - predicted_views
    return {
        "views_error": view_error,
        "views_error_rate": _relative_error(view_error, predicted_views),
        "likes_error": int(metric.likes or 0) - int(prediction.predicted_likes or 0),
        "favorites_error": int(metric.favorites or 0) - int(prediction.predicted_favorites or 0),
        "comments_error": int(metric.comments or 0) - int(prediction.predicted_comments or 0),
        "followers_error": int(metric.new_followers or 0) - int(prediction.predicted_new_followers or 0),
        "save_rate_error": round(rates["save_rate"] - float(prediction.predicted_save_rate or 0), 4),
        "like_rate_error": round(rates["like_rate"] - float(prediction.predicted_like_rate or 0), 4),
        "comment_rate_error": round(rates["comment_rate"] - float(prediction.predicted_comment_rate or 0), 4),
    }


def _build_prediction_calibration(rows: list[dict]) -> dict:
    predicted_rows = [item for item in rows if item.get("prediction") and item.get("prediction_error")]
    if not predicted_rows:
        return {
            "prediction_count": 0,
            "avg_abs_view_error_rate": None,
            "relative_error_sample_count": 0,
            "undefined_relative_error_count": 0,
            "avg_abs_views_error": None,
            "view_bias": "none",
            "underestimated_count": 0,
            "overestimated_count": 0,
            "top_misses": [],
        }

    view_error_rates = [
        float(item["prediction_error"]["views_error_rate"])
        for item in predicted_rows if item["prediction_error"]["views_error_rate"] is not None
    ]
    signed_view_errors = [int(item["prediction_error"].get("views_error") or 0) for item in predicted_rows]
    over_count = len([item for item in signed_view_errors if item < 0])
    under_count = len([item for item in signed_view_errors if item > 0])
    if under_count > over_count:
        bias = "underestimated"
    elif over_count > under_count:
        bias = "overestimated"
    else:
        bias = "balanced"

    misses = sorted(
        predicted_rows,
        key=lambda item: abs(int(item["prediction_error"]["views_error"])),
        reverse=True,
    )[:3]
    return {
        "prediction_count": len(predicted_rows),
        "avg_abs_view_error_rate": (
            round(sum(abs(item) for item in view_error_rates) / len(view_error_rates), 4)
            if view_error_rates else None
        ),
        "relative_error_sample_count": len(view_error_rates),
        "undefined_relative_error_count": len(predicted_rows) - len(view_error_rates),
        "avg_abs_views_error": round(sum(abs(item) for item in signed_view_errors) / len(signed_view_errors), 4),
        "view_bias": bias,
        "underestimated_count": under_count,
        "overestimated_count": over_count,
        "top_misses": [_topic_snapshot(item) for item in misses],
    }


def _relative_error(error: int, predicted: int) -> float | None:
    if predicted <= 0:
        return None
    return round(error / predicted, 4)


def _bias_label(value: str | None) -> str:
    labels = {
        "underestimated": "整体低估实际表现",
        "overestimated": "整体高估实际表现",
        "balanced": "高低估基本均衡",
        "none": "暂无可校准数据",
    }
    return labels.get(value or "none", "暂无可校准数据")


def _build_group_performance(stats_map: dict[str, dict[str, int | float]]) -> list[dict]:
    items: list[dict] = []
    for label, agg in stats_map.items():
        views = int(agg["views"]) if agg["views"] else 0
        impressions = int(agg["impressions"]) if agg["impressions"] else 0
        likes = int(agg["likes"])
        favorites = int(agg["favorites"])
        comments = int(agg["comments"])
        shares = int(agg["shares"])
        new_followers = int(agg["new_followers"])
        engagement = likes + favorites + comments + shares + new_followers
        items.append({
            "label": label,
            "posts": int(agg["posts"]),
            "views": views,
            "likes": likes,
            "favorites": favorites,
            "comments": comments,
            "shares": shares,
            "new_followers": new_followers,
            "engagement": engagement,
            "save_rate": _safe_rate(favorites, views),
            "like_rate": _safe_rate(likes, views),
            "comment_rate": _safe_rate(comments, views),
            "follow_conversion_rate": _safe_rate(new_followers, impressions or views),
        })
    items.sort(key=lambda item: (item["engagement"], item["views"], item["posts"]), reverse=True)
    return items


def _empty_stats() -> dict[str, int | float]:
    return {
        "posts": 0,
        "views": 0,
        "likes": 0,
        "favorites": 0,
        "comments": 0,
        "shares": 0,
        "new_followers": 0,
        "impressions": 0,
    }


def _accumulate_stats(stats: dict[str, dict[str, int | float]], key: str, metric: Metric) -> None:
    agg = stats[key]
    agg["posts"] += 1
    agg["views"] += int(metric.views)
    agg["likes"] += int(metric.likes)
    agg["favorites"] += int(metric.favorites)
    agg["comments"] += int(metric.comments)
    agg["shares"] += int(metric.shares)
    agg["new_followers"] += int(metric.new_followers)
    agg["impressions"] += int(metric.impressions or 0)


def _metric_rates(metric: Metric) -> dict[str, float]:
    views = int(metric.views)
    impressions = int(metric.impressions or 0)
    baseline = impressions if impressions > 0 else views
    return {
        "save_rate": _safe_rate(int(metric.favorites), views),
        "like_rate": _safe_rate(int(metric.likes), views),
        "comment_rate": _safe_rate(int(metric.comments), views),
        "follow_conversion_rate": (
            float(metric.follow_conversion_rate)
            if metric.follow_conversion_rate is not None
            else _safe_rate(int(metric.new_followers), baseline)
        ),
    }


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0
    return round(numerator / denominator, 4)
