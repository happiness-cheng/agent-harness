"""可信控制入口，普通输入队列和显式控制分离并按会话持久化。"""

from uuid import UUID


class SessionEvents:
    def __init__(self, sessions, context):
        self.sessions, self.context = sessions, sessions.check_access(context)

    def snapshot(self):
        return self.sessions.extension(self.context, "events", {
            "queue": [], "steers": [], "stop": False, "active": None,
            "status": "completed", "dispatch": True, "sequence": 0,
        })

    def _save(self, state):
        state["sequence"] += 1
        self.sessions.set_extension(self.context, "events", state)

    def enqueue(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("输入需要1到20000字符")
        with self.sessions.control(self.context):
            state = self.snapshot()
            if len(state["queue"]) >= 100:
                raise ValueError("队列已满")
            state["queue"].append(text); self._save(state)

    def next_input(self):
        with self.sessions.control(self.context):
            state = self.snapshot()
            if not state["dispatch"] or state["active"] or state["status"] != "completed" or not state["queue"]:
                return None
            text = state["queue"].pop(0); self._save(state)
            return text

    def started(self, task_id):
        with self.sessions.control(self.context):
            state = self.snapshot()
            if state["active"] is not None:
                raise RuntimeError("同一会话只能有一个活动控制循环")
            state.update(active=str(task_id), status="running", stop=False)
            self._save(state)

    def finished(self, status):
        with self.sessions.control(self.context):
            state = self.snapshot(); state.update(active=None, status=status, steers=[])
            if status == "cancelled":
                state["dispatch"] = False
            self._save(state)

    def stop(self):
        with self.sessions.control(self.context):
            state = self.snapshot(); state.update(stop=True, dispatch=False); self._save(state)

    def steer(self, task_id: UUID, text: str, *, scope_changed: bool):
        if not isinstance(text, str) or not text.strip() or len(text) > 20000 or type(scope_changed) is not bool:
            raise ValueError("引导参数无效")
        with self.sessions.control(self.context):
            state = self.snapshot()
            if state["active"] != str(task_id):
                raise PermissionError("引导不属于当前活动任务")
            state["steers"].append(text)
            if scope_changed:
                from min_agent.task_state import TaskController, GoalSpec
                work = self.sessions.work_state(self.context)
                old = work.proposal.goal if work.proposal else None
                TaskController(self.sessions).propose(self.context, GoalSpec(
                    objective=old.objective if old else text[:1000],
                    constraints=(*(old.constraints if old else ()), text[:1000]),
                    completion_criteria=old.completion_criteria if old else ("用户确认新范围",),
                    actions=(),
                ))
            self._save(state)

    def poll(self, task_id):
        with self.sessions.control(self.context):
            state = self.snapshot()
            if state["active"] != str(task_id):
                return True, []
            texts = state["steers"]
            if texts:
                state["steers"] = []; self._save(state)
            return state["stop"], texts

    def resume_dispatch(self):
        with self.sessions.control(self.context):
            state = self.snapshot()
            if state["active"]:
                raise RuntimeError("运行中不能恢复派发")
            state.update(dispatch=True, stop=False, status="completed"); self._save(state)
