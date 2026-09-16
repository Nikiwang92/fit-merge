# 仓库贡献指南

## 项目结构与模块组织

本仓库用于合并被拆分的运动记录，并保留原始运动数据。

- `data/fit/`：FIT 输入文件。`01.fit` 等数字命名文件属于高驰，`*_ACTIVITY.fit` 属于 Garmin。
- `fit_merge_tool/merge.py`：总入口，根据 `file_id.manufacturer` 识别并分发。
- `fit_merge_tool/merge_fit_coros_to_garmin.py`：高驰到佳明的专用合并逻辑。
- `fit_merge_tool/merge_fit_garmin_to_coros.py`：佳明到高驰的专用合并逻辑。
- `fit_merge_tool/test_merge.py`：FIT 合并回归测试。
- `merged/`：生成结果，不要手工修改。

高驰和佳明算法必须保留在各自模块中。总入口只能负责识别和分发，不能把两个品牌的专用算法混在同一个实现里。

## 构建、测试与开发命令

安装依赖：

```powershell
python -m pip install -r .\fit_merge_tool\requirements.txt
```

自动合并 `data/fit/` 下的有效文件组：

```powershell
python .\fit_merge_tool\merge.py
```

显式合并任意数量的 FIT 文件：

```powershell
python .\fit_merge_tool\merge.py .\data\fit\01.fit .\data\fit\02.fit -o .\merged\custom.fit
```

运行回归测试：

```powershell
python .\fit_merge_tool\test_merge.py
```

## 代码风格与命名规范

使用 Python 3.10 及以上版本，采用四空格缩进。函数、变量和模块使用 `snake_case`，类名使用 `PascalCase`，常量使用全大写。优先使用标准库的 `unittest` 和 `argparse`，没有明确需要时不要增加依赖。单个函数只处理一种活动格式。

## 测试要求

测试使用 `unittest`，并读取 `data/fit/` 下的真实 FIT 文件。测试文件命名为 `test_*.py`，测试方法以 `test_` 开头。每次修改合并逻辑都必须检查记录数、圈数、会话汇总、时间戳以及具有代表性的心率、距离和功率字段。

## 提交与 Pull Request 规范

当前目录没有 Git 历史，因此没有可沿用的提交格式。建议使用简短命令式提交信息，例如：

```text
merge: support three garmin fit files
```

Pull Request 应说明输入文件、日期平移方式、执行过的命令和测试结果。修改合并逻辑时，应附上关键字段对比或生成文件信息。

## 数据处理要求

不得覆盖源 FIT 文件。输出必须保持 CRC 有效，并保留支持的记录字段。为规避平台时间重叠检测而进行日期平移属于预期行为；如需修改，必须在文档和提交说明中明确记录。
