# jellyfin_tg_bot
# jellyfin_bot - 针对jellyfin服的开服机器人，实现功能如下：
-  通过机器人创建ellyfin账号，并同步设置好账号的相关权限-**全员可用**；
-  通过指令重置ellyfin账户密码，自定义设置账户密码-**全员可用**；
-  通过指令查询账户用户名-**全员可用**；
-  通过指令查询账户注册总数-**管理员可用**；
-  通过指令查询删除账号-**管理员可用**；
-  通过指令生成注册码-**全员可用**；

## 使用方法
- 拉取项目

```shell
git clone https://github.com/linlxh/jellyfin_tg_bot.git && cd jellyfin_tg_bot
```

- 修改项目根目录的`jellyfin_tg_bot.py`文件，填写TG机器人的API-TOKEN，jellyfin的地址JELLYFIN_URL，jellyfin的API,ADMIN_API_KEY,{tg管理员的id}ADMIN_IDS；

- 回到项目根目录，执行`python3 jellyfin_tg_bot.py`运行项目；

## 机器人指令大全：
- `/start` - 显示帮助信息

- `/register` - 通过邀请码注册账号

- `/query_credentials` - 查询账号信息

- `/admin_accounts` - 查看所有用户(管理员)

- `/generate_invite` - 生成邀请码(管理员)

- `/delete account` - 删除账号(管理员)


## 机器人进程守护可通过添加systemd服务来实现

```shell
cat >/etc/systemd/system/jellyfin_bot.service <<EOF
[Unit]
Description=jellyfin_bot
After=rc-local.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/root/jellyfin_tg_bot/ # 填写embybot目录路径
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
```
- 启动服务
```shell
systemctl start jellyfin_bot
```

- 设置开机启动
```shell
systemctl enable jellyfin_bot
```
- 机器人运行状态查看
```shell
systemctl status jellyfin_bot
```
