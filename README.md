# FreeBuds SE Windows 监控程序

这是一个用于监控华为 FreeBuds SE 耳机状态的 Windows 桌面应用程序。

## 功能特性

- 自动检测并连接 FreeBuds SE
- 实时显示充电盒电量
- 显示左右耳机电量
- 自动检测耳机盒开合状态

## 安装要求

- Python 3.8 或更高版本
- Windows 10 或更高版本
- 支持蓝牙 4.0 或更高版本

## 安装步骤

1. 克隆或下载此仓库
2. 安装依赖包：
   ```
   pip install -r requirements.txt
   ```
3. 运行程序：
   ```
   python main.py
   ```

## 注意事项

- 请确保电脑蓝牙已开启
- 首次连接时可能需要在 Windows 蓝牙设置中先配对设备
- 程序需要管理员权限来访问蓝牙设备 