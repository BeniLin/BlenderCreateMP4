"""系統端統一使用的例外類別。"""


class AnimatorError(Exception):
    """可預期、需要顯示給使用者的錯誤（設定錯誤、找不到工具、渲染失敗…）。"""


class ConfigError(AnimatorError):
    pass


class ToolError(AnimatorError):
    pass


class SpecError(AnimatorError):
    """spec 驗證失敗；issues 為所有錯誤的清單。"""

    def __init__(self, issues):
        self.issues = list(issues)
        lines = "\n".join(f"  - {i}" for i in self.issues)
        super().__init__(f"spec 驗證失敗（{len(self.issues)} 個錯誤）：\n{lines}")


class RenderError(AnimatorError):
    pass
