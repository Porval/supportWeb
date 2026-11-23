# supportWeb

价探活动工作台，包含活动提报、识别、审核、品牌与商品视图，以及管理员与活动 Agent 的对话。本仓库从线上版本 `jt024-hosted-20261123.8` 整理而来。

## 代码入口

`front/` 是浏览器前端。`server.py` 提供工作台 HTTP 接口，`hosted_otp.py` 和 `workbench_sms_gateway.py` 负责短信登录。`runtime/` 包含业务逻辑和接口契约。`agent-service/` 与 `agent-chat-runtime/` 分别提供活动识别和对话。

## 运行登录回归

在仓库根目录使用 Python 3.11 执行。以下号码和域名仅用于隔离测试。短信网关使用内存模拟，HTTP 测试使用临时 SQLite 数据库和回环端口。

```sh
export WORKBENCH_ADMIN_PHONE=13900001234
export WORKBENCH_PUBLIC_ORIGIN=https://workbench.example.invalid
python3 -B -m unittest -q test_hosted_otp test_workbench_sms_gateway test_bound_gateway_transport
python3 -B check_delivery_http.py
```

## 已有部署接入

运行入口是 `bash start.sh`。部署时设置 `HOST=127.0.0.1`、`PORT`、`ACTIVITY_DATA_DIR`、`WORKBENCH_PUBLIC_ORIGIN` 和 `WORKBENCH_ADMIN_PHONE`。管理员号码须与部署侧 `bootstrap.json` 的 `username` 一致。通过私有配置提供该文件内的管理员校验参数，以及 `workbench_sms_route.json` 中的 apl1 短信路由。短信配置使用正式模板、`mode=remote`、`phoneField=number`、`codeField=code` 和 `loginEnabled=true`。

活动 Agent 依赖服务账户的私有配置、已初始化的 MySQL 和已授权的数据源。真实活动附件、历史人工确认、原件指纹、线上数据库与密钥由各部署实例保管。仓库中保留识别规则、提示词和接口代码。生产切换仍使用独立的发布流程。
