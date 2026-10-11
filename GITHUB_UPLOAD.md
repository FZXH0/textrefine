# 将 TextRefine 上传到 GitHub

仓库应命名为 `textrefine`，可见性选择 **Public**，源码使用 MIT 许可证。本目录已准备好 README、LICENSE、.gitignore 和自动测试。

## 第一次上传（Git 命令）

1. 登录 GitHub，点击右上角 **+ → New repository**。
2. Repository name 填 `textrefine`，选择 Public。
3. 本地已有 README、许可证和忽略规则，不勾选 GitHub 初始化这些文件，然后点击 Create repository。
4. 在本项目目录运行，替换下面的 `YOUR_USERNAME`：

```bash
git init -b main
git add .
git commit -m "Initial open-source release of TextRefine"
git remote add origin https://github.com/YOUR_USERNAME/textrefine.git
git push -u origin main
```

本目录可能已初始化 Git；此时可跳过 `git init`。若没有配置提交者身份，先在本仓库设置：

```bash
git config user.name "YOUR_NAME"
git config user.email "YOUR_GITHUB_NOREPLY_EMAIL"
```

建议使用 GitHub Settings → Emails 中的 noreply 邮箱。GitHub 不接受账号密码作为 HTTPS Git 密码；可用 GitHub CLI 的 `gh auth login`、SSH 或 Git 凭据管理器完成认证。不要把个人令牌写进仓库 URL、源码或聊天。

## 使用网页上传

新建公开仓库后，选择 **uploading an existing file**，上传本项目里的源码、文档、tests 和 .github 目录，填写提交说明并点击 Commit changes。请确认隐藏文件 .gitignore 和 .github/workflows/tests.yml 也已上传。

不要上传 .git、__pycache__、API 配置、历史、草稿或 .env；不要只上传 ZIP 代替源码，否则 GitHub 无法直接浏览和运行测试。

## 后续更新

```bash
git add .
git commit -m "Describe the change"
git push
```

上传后检查仓库顶部显示 Public，LICENSE 显示 MIT，Actions 中 Tests 工作流运行。可从 Releases 创建版本并附加源码 ZIP。
