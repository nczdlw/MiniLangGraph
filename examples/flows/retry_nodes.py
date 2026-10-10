"""条件重试流程的节点 / 路由器定义（被 retry_flow.json 通过 imports 引用）。

由 @register_node / @register_router 装饰器注册，loader 导入本模块即完成装配。
"""

from minigraph.registry import register_node, register_router


@register_node("attempt_task")
def attempt_task(state: dict) -> dict:
    """模拟一次任务尝试：前两次失败，第三次成功。"""
    tries = state.get("tries", 0) + 1
    ok = tries >= 3
    return {"tries": tries, "status": "ok" if ok else "fail"}


@register_router("route_retry")
def route_retry(state: dict) -> str:
    """路由：成功则进入 finalize，否则回到 attempt 重试（自环）。"""
    return "ok" if state.get("status") == "ok" else "retry"


@register_node("finalize")
def finalize(state: dict) -> dict:
    """收尾：记录最终尝试次数与成功状态。"""
    return {"final_tries": state.get("tries", 0), "done": True}
