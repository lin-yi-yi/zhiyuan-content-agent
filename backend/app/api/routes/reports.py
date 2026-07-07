"""7天复盘 API"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
    angle_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)
    template_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)
    content_type_stats: dict[str, dict[str, int | float]] = defaultdict(_empty_stats)

    for log in logs:
        log_date = (log.published_at or log.created_at).date()
        if not (body.start_date <= log_date <= body.end_date):
            continue

        draft = db.query(Draft).filter(Draft.id == log.draft_id).first()
        topic = db.query(Topic).filter(Topic.id == draft.topic_id).first() if draft else None
        latest_metric = (
            db.query(Metric)
            .filter(Metric.publish_log_id == log.id)
            .order_by(Metric.collected_at.desc())
            .first()
        )

        metric = latest_metric or Metric(
            publish_log_id=log.id,
            views=0,
            likes=0,
            favorites=0,
            comments=0,
            shares=0,
            new_followers=0,
        )

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

    total_posts = len(rows)
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
    recommendations = _build_recommendations(
        rows,
        angle_performance,
        template_performance,
        content_type_performance,
        prediction_calibration,
    )
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
            },
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
            "本周期还没有可复盘的数据，先保证每周至少发布 3 条图文。",
            "发布前先记录预测，再录入真实数据，后续才能校准选题判断。",
            "每条内容发布后记录浏览、点赞、收藏、评论和新增粉丝。",
            "下周优先做 AI 工作流实操、Agent 开发日志和普通人提效案例。",
        ]

    best = rows[0]
    best_angle = best["topic"].content_angle if best["topic"] else "实操案例"
    best_template = best["template"]
    weak_angle = angle_performance[-1]["label"] if len(angle_performance) > 1 else "未标注角度"
    weak_template = template_performance[-1]["label"] if len(template_performance) > 1 else "未标注模板"
    high_save = [item for item in rows if item["save_rate"] >= 0.05]
    high_engage = [
        item
        for item in rows
        if item["save_rate"] >= 0.05 and item["comment_rate"] >= 0.02
    ]

    top_content_type = content_type_performance[0]["label"] if content_type_performance else "未标注类型"
    if content_type_performance and len(content_type_performance) > 1:
        weak_content_type = content_type_performance[-1]["label"]
    else:
        weak_content_type = "未标注类型"

    items = [
        f"继续做“{best_angle or '实操案例'}”方向，当前最佳内容的收藏率为 {best['save_rate'] * 100:.1f}%。",
        f"收藏率高的模板是“{best_template}”，可保留其封面节奏和卡片密度。",
        f"下周建议在“{weak_template}”和“{weak_angle}”维度各补 1 条对照数据，验证是否为内容结构问题。",
        f"下周优先测试 2-3 个 {top_content_type} 方向选题，重点观察评论率和收藏率是否同时提升。",
        f"内容类型“{weak_content_type}”本周表现偏弱，先减少发布频次，重点做 2 期验证再决定是否继续。",
        "评论区要主动追问用户最想自动化的职场任务，作为下组选题来源。",
        f"本周期共有 {len(high_save)} 条内容收藏率超过 5%，其中 {len(high_engage)} 条评论率也较高，适合做系列复用。",
    ]
    if prediction_calibration.get("prediction_count"):
        avg_error = float(prediction_calibration.get("avg_abs_view_error_rate") or 0)
        bias = _bias_label(prediction_calibration.get("view_bias"))
        items.insert(2, f"发布前预测本周期平均浏览误差约 {avg_error * 100:.1f}%，整体偏差为“{bias}”，下次先校准这一侧。")
    else:
        items.insert(2, "下周至少为 3 条内容记录发布前预测，复盘时才能判断选题直觉是否变准。")
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
) -> str:
    best_title = best_items[0]["title"] if best_items else "暂无"
    lines = [
        f"{start_date} 至 {end_date} 复盘",
        "",
        f"本周期发布 {total_posts} 条内容，总浏览 {total_views}，点赞 {total_likes}，收藏 {total_favorites}，评论 {total_comments}，新增粉丝 {total_followers}。",
        f"收藏率 {total_save_rate * 100:.1f}%，点赞率 {total_like_rate * 100:.1f}%，评论率 {total_comment_rate * 100:.1f}%，关注转化率 {total_follow_conversion * 100:.1f}%。",
        f"表现最好的选题：{best_title}。",
        "",
        "初步规律：",
        "1. 高收藏内容通常需要明确步骤、真实任务和可复制清单。",
        "2. 泛泛解释 AI 概念的内容，需要转成具体工作流才更适合小红书图文。",
        "3. 发布后应重点观察收藏率、评论率，而不只看浏览量。",
    ]
    if prediction_calibration.get("prediction_count"):
        lines.extend([
            f"4. 本周期有 {prediction_calibration['prediction_count']} 条内容可做预测校准，平均浏览误差约 {prediction_calibration['avg_abs_view_error_rate'] * 100:.1f}%。",
            f"5. 预测偏差方向：{_bias_label(prediction_calibration.get('view_bias'))}。",
        ])
    else:
        lines.append("4. 当前缺少发布前预测，后续应先记录预测再发布，才能校准选题判断。")
    lines.extend([
        "",
        "下周建议：",
    ])
    lines.extend([f"- {item}" for item in recommendations])
    return "\n".join(lines)


def _prediction_for_log(log: PublishLog, db: Session) -> ContentPrediction | None:
    linked = (
        db.query(ContentPrediction)
        .filter(ContentPrediction.publish_log_id == log.id)
        .order_by(ContentPrediction.created_at.desc(), ContentPrediction.id.desc())
        .first()
    )
    if linked:
        return linked
    return (
        db.query(ContentPrediction)
        .filter(
            ContentPrediction.draft_id == log.draft_id,
            ContentPrediction.platform == log.platform,
            ContentPrediction.publish_log_id.is_(None),
        )
        .order_by(ContentPrediction.created_at.desc(), ContentPrediction.id.desc())
        .first()
    )


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
            "avg_abs_view_error_rate": 0,
            "view_bias": "none",
            "underestimated_count": 0,
            "overestimated_count": 0,
            "top_misses": [],
        }

    view_error_rates = [float(item["prediction_error"].get("views_error_rate") or 0) for item in predicted_rows]
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
        key=lambda item: abs(float(item["prediction_error"].get("views_error_rate") or 0)),
        reverse=True,
    )[:3]
    return {
        "prediction_count": len(predicted_rows),
        "avg_abs_view_error_rate": round(sum(abs(item) for item in view_error_rates) / len(view_error_rates), 4),
        "view_bias": bias,
        "underestimated_count": under_count,
        "overestimated_count": over_count,
        "top_misses": [_topic_snapshot(item) for item in misses],
    }


def _relative_error(error: int, predicted: int) -> float:
    if predicted <= 0:
        return 0
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
