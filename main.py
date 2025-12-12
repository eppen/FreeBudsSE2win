import asyncio
import logging
import struct
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient, BleakError
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QMessageBox, QPushButton, QHBoxLayout, QScrollArea, QFrame
from PyQt6.QtCore import QTimer, QThread, Qt
import sys

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bluetooth_scanner.log')
    ]
)
logger = logging.getLogger(__name__)

# 设备信息
DEVICE_NAMES = [
    "HUAWEI FreeBuds SE 2",
    "FreeBuds SE 2"
]
DEVICE_ADDRESSES = [
    "90:F6:44:AA:EE:67",  # 需要替换为您耳机的实际MAC地址
    "90-F6-44-AA-EE-67",  # 带横杠格式
    "90F644AAEE67"        # 无分隔符格式
]
HUAWEI_COMPANY_ID = 0x0156  # 华为公司ID
TARGET_SERVICE_UUIDS = [
    "0000180f-0000-1000-8000-00805f9b34fb",  # 电池服务
    "0000180a-0000-1000-8000-00805f9b34fb",  # 设备信息服务
    "0000111e-0000-1000-8000-00805f9b34fb",  # 华为自定义服务
]

def normalize_address(address):
    """标准化MAC地址格式"""
    # 移除所有分隔符
    clean_addr = address.replace(":", "").replace("-", "").upper()
    # 返回三种格式
    return [
        ":".join(clean_addr[i:i+2] for i in range(0, 12, 2)),  # 带冒号
        "-".join(clean_addr[i:i+2] for i in range(0, 12, 2)),  # 带横杠
        clean_addr  # 无分隔符
    ]

def parse_manufacturer_data(data):
    """解析制造商数据"""
    try:
        if len(data) < 2:
            return "数据长度不足"
            
        result = []
        # 尝试解析常见的数据格式
        if len(data) >= 2:
            flags = data[0]
            result.append(f"标志位: 0x{flags:02x}")
            
        if len(data) >= 4:
            value = struct.unpack("<H", data[2:4])[0]
            result.append(f"值: {value}")
            
        # 添加原始数据
        result.append(f"原始数据: {data.hex()}")
        
        return "\n".join(result)
    except Exception as e:
        return f"解析错误: {e}"

class AsyncThread(QThread):
    def __init__(self):
        super().__init__()
        self.loop = None
        self.running = True
        self.retry_count = 0
        self.max_retries = 3

    def run(self):
        while self.retry_count < self.max_retries:
            try:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()
                break  # 如果正常运行，跳出循环
            except Exception as e:
                self.retry_count += 1
                logger.error(f"异步线程出错 (尝试 {self.retry_count}/{self.max_retries}): {e}")
                if self.loop:
                    try:
                        self.loop.close()
                    except:
                        pass
                    self.loop = None
                time.sleep(1)  # 等待1秒后重试
        
        if self.retry_count >= self.max_retries:
            logger.error("异步线程重试次数已达上限")

    def stop(self):
        self.running = False
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception as e:
                logger.error(f"停止异步线程时出错: {e}")

class DeviceWidget(QFrame):
    """单个设备的显示组件"""
    def __init__(self, device_name, device_info, parent=None):
        if QThread.currentThread() is not QApplication.instance().thread():
            logger.warning("DeviceWidget 不在主线程中创建")
            return
            
        super().__init__(parent)
        self.device_info = device_info
        
        # 创建水平布局
        layout = QHBoxLayout(self)
        
        # 设备名称标签
        self.name_label = QLabel(device_name)
        layout.addWidget(self.name_label, stretch=1)
        
        # 详情按钮
        self.detail_button = QPushButton("查看详情")
        self.detail_button.clicked.connect(self.show_details)
        layout.addWidget(self.detail_button)
        
        # 设置边框样式
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        
    def show_details(self):
        """显示设备详细信息"""
        # 确保在主线程中显示对话框
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda: self.show_details())
            return
            
        msg = QMessageBox()
        msg.setWindowTitle("设备详细信息")
        msg.setText(self.device_info)
        msg.exec()

# 在 FreeBudsWindow 类中添加错误处理装饰器
def catch_exception(f):
    async def wrapper(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except asyncio.CancelledError:
            logger.debug("操作被取消")
            raise
        except Exception as e:
            logger.exception(f"执行 {f.__name__} 时出错: {str(e)}")
            # 获取 self 参数（第一个参数）
            if args:
                self = args[0]
                if hasattr(self, 'debug_label'):
                    self.debug_label.setText(f"调试信息: {f.__name__} 出错 - {str(e)}")
    return wrapper

class FreeBudsWindow(QMainWindow):
    def __init__(self):
        try:
            super().__init__()
            logger.debug("开始初始化主窗口...")
            self.setWindowTitle("FreeBuds SE 2 监控")
            self.setGeometry(100, 100, 600, 800)  # 增加窗口大小

            # 创建主窗口部件和布局
            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            layout = QVBoxLayout(central_widget)

            # 创建按钮布局
            button_layout = QHBoxLayout()
            
            # 创建扫描控制按钮
            self.scan_button = QPushButton("暂停扫描")
            self.scan_button.clicked.connect(self.toggle_scanning)
            button_layout.addWidget(self.scan_button)
            
            # 添加按钮布局到主布局
            layout.addLayout(button_layout)

            # 创建状态标签
            self.status_label = QLabel("状态: 未连接")
            self.left_battery_label = QLabel("左耳机电量: 未知")
            self.right_battery_label = QLabel("右耳机电量: 未知")
            self.case_battery_label = QLabel("充电盒电量: 未知")
            self.debug_label = QLabel("调试信息: 等待扫描...")
            self.scan_time_label = QLabel("上次扫描时间: 无")

            # 添加标签到布局
            layout.addWidget(self.status_label)
            layout.addWidget(self.left_battery_label)
            layout.addWidget(self.right_battery_label)
            layout.addWidget(self.case_battery_label)
            layout.addWidget(self.debug_label)
            layout.addWidget(self.scan_time_label)

            # 创建设备列表区域
            devices_group = QWidget()
            self.devices_layout = QVBoxLayout(devices_group)
            
            # 创建滚动区域
            scroll = QScrollArea()
            scroll.setWidget(devices_group)
            scroll.setWidgetResizable(True)
            scroll.setMinimumHeight(300)  # 设置最小高度
            
            # 添加设备列表到主布局
            layout.addWidget(QLabel("发现的设备:"))
            layout.addWidget(scroll)

            # 设备详细信息标签
            self.device_info_label = QLabel("设备详细信息: 无")
            layout.addWidget(self.device_info_label)

            # 初始化其他组件...
            self.init_other_components()

            logger.debug("主窗口初始化完成")
        except Exception as e:
            logger.exception("初始化主窗口时出错")
            QMessageBox.critical(None, "错误", f"初始化失败: {str(e)}")
            raise

    def init_other_components(self):
        """初始化其他组件"""
        # 初始化异步线程
        self.async_thread = AsyncThread()
        self.async_thread.start()
        logger.debug("异步线程已初始化")

        # 添加一个标志来跟踪扫描状态
        self.scanning_enabled = True
        
        # 设置定时器定期扫描设备
        self.timer = QTimer()
        self.timer.timeout.connect(self.start_scan)
        self.timer.start(3000)  # 每3秒扫描一次

        # 初始化变量
        self.last_connected_address = None
        self.client = None
        self.found_devices = []
        self.connection_retries = 0
        self.max_retries = 5
        self.retry_delay = 2
        self.min_rssi = -80
        self.device_widgets = {}  # 存储设备部件

    def update_device_list(self, devices_info):
        """更新设备列表显示"""
        try:
            # 使用 moveToThread 确保在主线程中更新 UI
            def update_ui():
                try:
                    # 清除旧的设备部件
                    for widget in self.device_widgets.values():
                        try:
                            widget.setParent(None)
                            widget.deleteLater()  # 确保正确删除部件
                        except Exception as e:
                            logger.error(f"清除设备部件时出错: {e}")
                    self.device_widgets.clear()

                    # 添加新的设备部件
                    for device_name, device_info in devices_info:
                        try:
                            if device_name not in self.device_widgets:
                                widget = DeviceWidget(device_name, device_info)
                                self.devices_layout.addWidget(widget)
                                self.device_widgets[device_name] = widget
                        except Exception as e:
                            logger.error(f"添加设备部件时出错: {e}")
                            continue
                except Exception as e:
                    logger.error(f"更新UI时出错: {e}")

            # 在主线程中执行更新
            if QThread.currentThread() is QApplication.instance().thread():
                update_ui()
            else:
                QTimer.singleShot(0, update_ui)

        except Exception as e:
            logger.error(f"更新设备列表时出错: {e}")

    def is_target_device(self, device):
        """检查是否是目标设备"""
        if not device.name and not device.address:
            return False
            
        # 记录设备详细信息
        device_info = []
        device_info.append(f"名称: {device.name or '未知'}")
        device_info.append(f"地址: {device.address}")
        device_info.append(f"RSSI: {device.rssi}")
        
        # 检查制造商数据
        if device.metadata.get("manufacturer_data"):
            device_info.append("制造商数据:")
            for company_id, data in device.metadata["manufacturer_data"].items():
                device_info.append(f"  公司ID: {company_id:04x}")
                device_info.append(f"  解析数据:")
                device_info.append("    " + parse_manufacturer_data(data))
                # 检查是否是华为设备
                if company_id == HUAWEI_COMPANY_ID:
                    logger.debug(f"发现华为设备: {device.name} ({device.address})")
                    # 仅记录，不直接作为目标设备返回
        
        # 检查广播数据
        if device.metadata.get("uuids"):
            device_info.append("服务UUID:")
            for uuid in device.metadata["uuids"]:
                device_info.append(f"  {uuid}")
                # 检查是否包含目标服务
                if uuid.lower() in TARGET_SERVICE_UUIDS:
                    logger.debug(f"发现包含目标服务的设备: {device.name} ({device.address})")
                    # 仅记录，不直接作为目标设备返回
            
        # 检查设备地址
        device_addresses = normalize_address(device.address)
        if any(addr in device_addresses for addr in DEVICE_ADDRESSES):
            logger.debug(f"通过MAC地址匹配到设备: {device.name} ({device.address})")
            self.device_info_label.setText("设备详细信息:\n" + "\n".join(device_info))
            return True
            
        # 检查设备名称
        if device.name:
            device_name = device.name.lower()
            if any(name.lower() in device_name for name in DEVICE_NAMES):
                logger.debug(f"通过设备名称匹配到设备: {device.name} ({device.address})")
                self.device_info_label.setText("设备详细信息:\n" + "\n".join(device_info))
                return True
        
        # 更新设备详细信息显示
        self.device_info_label.setText("设备详细信息:\n" + "\n".join(device_info))
        return False

    @catch_exception
    async def scan_devices(self):
        try:
            logger.debug("开始扫描设备...")
            self.debug_label.setText("调试信息: 正在扫描设备...")
            scan_start_time = datetime.now()

            # 使用 BleakScanner 扫描设备
            devices = await BleakScanner.discover(timeout=5.0)
            
            # 更新扫描时间
            scan_end_time = datetime.now()
            scan_duration = (scan_end_time - scan_start_time).total_seconds()
            self.scan_time_label.setText(
                f"上次扫描时间: {scan_end_time.strftime('%H:%M:%S')}\n"
                f"扫描用时: {scan_duration:.1f}秒"
            )
            
            # 清空设备列表
            self.found_devices = []
            target_devices_info = []
            other_devices_info = []
            target_device = None
            
            # 收集所有发现的设备信息
            for device in devices:
                try:
                    if device.name or device.address:
                        logger.debug(f"处理设备: {device.name or '未知'} ({device.address})")
                        self.found_devices.append(device)
                        
                        device_name = f"{device.name or '未知'} ({device.address})"
                        details = self.get_device_details(device)
                        
                        # 检查是否是目标设备
                        if self.is_target_device(device):
                            target_devices_info.append((f"[目标设备] {device_name}", details))
                            target_device = device
                            self.status_label.setText(f"状态: 现发目标设备 - {device.name or device.address}")
                            # 停止扫描
                            self.scanning_enabled = False
                            self.timer.stop()
                            self.scan_button.setText("恢复扫描")
                            break  # 找到目标设备后立即停止扫描
                        else:
                            other_devices_info.append((device_name, details))
                except Exception as e:
                    logger.error(f"处理设备时出错: {e}")
                    continue

            # 更新设备列表显示
            try:
                devices_info = []
                
                # 添加目标设备（如果有）
                if target_devices_info:
                    devices_info.append(("=== 目标设备 ===", ""))
                    devices_info.extend(target_devices_info)
                
                # 添加其他设备
                if other_devices_info:
                    if devices_info:  # 如果已经有目标设备，添加分隔
                        devices_info.append(("", ""))
                    devices_info.append(("=== 其他设备 ===", ""))
                    devices_info.extend(other_devices_info)
                
                if devices_info:
                    logger.debug(f"更新设备列表，目标设备: {len(target_devices_info)}，其他设备: {len(other_devices_info)}")
                    self.update_device_list(devices_info)
                else:
                    logger.debug("未发现任何设备")
                    self.update_device_list([("无设备", "未发现任何蓝牙设备")])
            except Exception as e:
                logger.error(f"更新设备列表显示时出错: {e}")

            # 如果找到目标设备，显示连接确认对话框
            if target_device:
                # 检查是否已经连接到该设备
                if self.client and self.client.is_connected and self.client.address == target_device.address:
                    logger.debug(f"已连接到目标设备: {target_device.address}，跳过连接对话框")
                    self.status_label.setText(f"状态: 已连接 - {target_device.name or target_device.address}")
                    self.debug_label.setText("调试信息: 已连接，正在更新数据...")
                    
                    # 停止扫描
                    self.scanning_enabled = False
                    self.timer.stop()
                    self.scan_button.setText("恢复扫描")
                    
                    # 更新电量
                    await self.read_battery_level()
                    return

                # 在主线程中显示对话框
                should_connect = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.show_connection_dialog(target_device)
                )
                
                # 更新调试信息
                self.debug_label.setText(f"调试信息: 用户选择{'连接' if should_connect else '取消连接'}")
                
                if should_connect:
                    try:
                        # 显示连接中状态
                        self.status_label.setText(f"状态: 正在连接 {target_device.name or target_device.address}...")
                        self.debug_label.setText("调试信息: 正在连接设备...")
                        
                        # 执行连接
                        await self.connect_device(target_device)
                        
                        # 更新状态
                        if self.client and self.client.is_connected:
                            self.status_label.setText(f"状态: 已连接 - {target_device.name or target_device.address}")
                            self.debug_label.setText("调试信息: 连接成功")
                        else:
                            self.status_label.setText("状态: 连接失败")
                            self.debug_label.setText("调试信息: 连接失败，请重试")
                            
                    except Exception as e:
                        logger.error(f"连接设备失败: {e}")
                        self.debug_label.setText(f"调试信息: 连接失败 - {str(e)}")
                        self.status_label.setText("状态: 连接失败")
                        # 恢复扫描
                        QTimer.singleShot(1000, self.resume_scanning)
                else:
                    self.debug_label.setText("调试信息: 用户取消了连接")
                    # 恢复扫描
                    QTimer.singleShot(1000, self.resume_scanning)
        
        except Exception as e:
            logger.exception("扫描设备时出错")
            self.debug_label.setText(f"调试信息: 扫描出错 - {str(e)}")
            QTimer.singleShot(1000, self.resume_scanning)

    async def connect_device(self, device):
        try:
            # 如果已经连接到同一设备，直接返回
            if self.client and self.client.is_connected and self.client.address == device.address:
                logger.debug("已连接到该设备，无需重新连接")
                self.status_label.setText(f"状态: 已连接 - {device.name or device.address}")
                await self.read_battery_level()
                return

            # 如果已经连接到其他设备，先断开
            if self.client and self.client.is_connected:
                logger.debug("断开现有连接")
                await self.client.disconnect()

            # 检查Windows是否已连接该设备
            try:
                self.client = BleakClient(device.address)
                if await self.client.connect():
                    logger.debug("Windows已连接设备，直接读取电量")
                    self.status_label.setText(f"状态: Windows已连接 - {device.address}")
                    await self.read_battery_level()
                    return
            except Exception as e:
                logger.debug(f"Windows连接状态检查失败: {e}")
            
            # 连接新设备
            logger.debug(f"尝试连接设备: {device.name or '未知'} ({device.address})")
            self.debug_label.setText(f"调试信息: 正在连接 {device.name or device.address}")
            
            self.client = BleakClient(device.address, timeout=20.0)
            await self.client.connect()
            
            if self.client.is_connected:
                logger.debug(f"成功连接到设备: {device.name or device.address}")
                self.status_label.setText(f"状态: 已连接 - {device.name or device.address}")
                self.last_connected_address = device.address
                self.connection_retries = 0  # 重置重试次数
                self.debug_label.setText(f"调试信息: 已连接到 {device.name or device.address}，正在读取服务...")

                # 读取所有服务和特征值
                for service in self.client.services:
                    logger.debug(f"服务 UUID: {service.uuid}")
                    for char in service.characteristics:
                        if char.readable:
                            try:
                                value = await self.client.read_gatt_char(char.uuid)
                                logger.debug(f"特征值 {char.uuid}: {value}")
                                
                                # 如果是电池服务，更新UI
                                if service.uuid == TARGET_SERVICE_UUIDS[0]:  # 电池服务
                                    self.update_battery_level(value[0])
                            except Exception as e:
                                logger.error(f"读取特征值出错: {e}")
            else:
                logger.error("连接失败")
                self.status_label.setText("状态: 连接失败")
                self.debug_label.setText("调试信息: 连接失败，请检查设备状态")
                
                # 如果重试次数未超过最大值，则重试
                if self.connection_retries < self.max_retries:
                    self.connection_retries += 1
                    logger.debug(f"正在重试连接 ({self.connection_retries}/{self.max_retries})")
                    self.debug_label.setText(f"调试信息: 正在重试连接 ({self.connection_retries}/{self.max_retries})")
                    await asyncio.sleep(self.retry_delay)  # 增加重试延迟
                    if device.rssi >= self.min_rssi:  # 检查信号强度
                        await self.connect_device(device)
                    else:
                        logger.debug(f"信号强度不足: {device.rssi} < {self.min_rssi}")
                        self.debug_label.setText(f"调试信息: 信号强度不足 ({device.rssi}dBm)")
                else:
                    self.connection_retries = 0
                    logger.error("连接重试次数已达上限")
                    self.debug_label.setText("调试信息: 连接重试次数已达上限，请检查设备状态")

        except Exception as e:
            logger.error(f"连接设备时出错: {e}")
            self.debug_label.setText(f"调试信息: 连接失败 - {str(e)}")
            self.status_label.setText("状态: 连接失败")
            
            # 检查特定异常类型并显示更详细的错误信息
            error_msg = f"调试信息: 连接失败 - {str(e)}"
            if isinstance(e, asyncio.TimeoutError):
                error_msg = "调试信息: 连接超时，请确保设备在范围内"
            elif isinstance(e, BleakError):
                error_msg = f"调试信息: 蓝牙连接错误 ({str(e)})，请检查:\n1. 蓝牙是否开启\n2. 设备是否在范围内\n3. 设备是否可被发现"
            elif isinstance(e, RuntimeError):
                error_msg = f"调试信息: 运行时错误 ({str(e)})，请尝试重启程序"
            
            self.debug_label.setText(error_msg)
            
            # 如果重试次数未超过最大值，则重试
            if self.connection_retries < self.max_retries:
                self.connection_retries += 1
                logger.debug(f"正在重试连接 ({self.connection_retries}/{self.max_retries})")
                self.debug_label.setText(f"调试信息: 正在重试连接 ({self.connection_retries}/{self.max_retries})")
                await self.connect_device(device)
            else:
                self.connection_retries = 0
                logger.error("连接重试次数已达上限")
                self.debug_label.setText("调试信息: 连接重试次数已达上限，请检查设备状态")

    @catch_exception
    async def read_battery_level(self):
        if not self.client:
            logger.error("未初始化蓝牙客户端")
            return
            
        try:
            if not self.client.is_connected:
                logger.error("设备未连接")
                self.debug_label.setText("调试信息: 设备未连接")
                return
                
            services = await self.client.get_services()
            battery_service = None
            
            for service in services:
                if service.uuid == TARGET_SERVICE_UUIDS[0]:
                    battery_service = service
                    break
            
            if not battery_service:
                logger.error("未找到电池服务")
                self.debug_label.setText("调试信息: 未找到电池服务")
                return
                
            for char in battery_service.characteristics:
                if char.readable:
                    try:
                        value = await self.client.read_gatt_char(char.uuid)
                        if value and len(value) > 0:
                            self.update_battery_level(value[0])
                            return
                    except BleakError as e:
                        logger.error(f"读取电池特征值失败: {e}")
                        continue
            
            self.debug_label.setText("调试信息: 无法读取电池电量")
        except Exception as e:
            logger.exception("读取电池电量时出错")
            self.debug_label.setText(f"调试信息: 读取电量失败 - {str(e)}")

    def update_battery_level(self, level):
        """更新电池电量显示"""
        # 添加电量更新频率控制
        if not hasattr(self, 'last_update_time'):
            self.last_update_time = 0
            self.last_level = 0
            
        current_time = time.time()
        if current_time - self.last_update_time > 5 or abs(level - self.last_level) >= 5:
            self.left_battery_label.setText(f"左耳机电量: {level}%")
            self.right_battery_label.setText(f"右耳机电量: {level}%")
            self.case_battery_label.setText(f"充电盒电量: {level}%")
            self.last_update_time = current_time
            self.last_level = level

    def start_scan(self):
        """开始扫描"""
        try:
            if not self.scanning_enabled:
                logger.debug("扫描已禁用")
                return
                
            if not self.async_thread or not self.async_thread.loop:
                logger.error("异步线程或事件循环未初始化")
                self.init_other_components()
                return
                
            asyncio.run_coroutine_threadsafe(self.scan_devices(), self.async_thread.loop)
        except Exception as e:
            logger.exception("启动扫描时出错")
            self.debug_label.setText(f"调试信息: 启动扫描出错 - {str(e)}")
            QTimer.singleShot(3000, self.resume_scanning)

    def resume_scanning(self):
        """恢复扫描"""
        try:
            # 恢复扫描
            logger.debug("恢复扫描")
            self.status_label.setText("状态: 扫描已恢复")
            self.scanning_enabled = True
            if not self.timer.isActive():
                self.timer.start()
            self.scan_button.setText("暂停扫描")
            self.debug_label.setText("调试信息: 已恢复扫描...")
        except Exception as e:
            logger.exception("恢复扫描时出错")
            self.debug_label.setText(f"调试信息: 恢复扫描出错 - {str(e)}")

    def closeEvent(self, event):
        try:
            logger.debug("正在关闭应用程序...")
            self.timer.stop()
            
            # 确保断开蓝牙连接
            if self.client and self.client.is_connected:
                try:
                    if self.async_thread and self.async_thread.loop:
                        future = asyncio.run_coroutine_threadsafe(
                            self.client.disconnect(), 
                            self.async_thread.loop
                        )
                        future.result(timeout=2.0)  # 等待最多2秒
                except Exception as e:
                    logger.error(f"断开蓝牙连接时出错: {e}")
            
            # 停止异步线程
            if self.async_thread:
                self.async_thread.stop()
                self.async_thread.wait(timeout=2000)  # 等待最多2秒
            
            event.accept()
        except Exception as e:
            logger.exception("关闭应用程序时出错")
            event.accept()

    def show_connection_dialog(self, device):
        """显示连接确认对话框"""
        msg = QMessageBox()
        msg.setWindowTitle("发现设备")
        msg.setText(f"发现FreeBuds SE 2设备：{device.name or device.address}")
        msg.setInformativeText("是否尝试连接？\n\n注意：如果设备已连接到Windows，需要先断开连接。")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        # 设置对话框为模态
        msg.setWindowModality(Qt.WindowModality.ApplicationModal)
        
        return msg.exec() == QMessageBox.StandardButton.Yes

    def toggle_scanning(self):
        """切换扫描状态"""
        if self.scanning_enabled:
            # 暂停扫描
            self.scanning_enabled = False
            self.timer.stop()
            self.scan_button.setText("恢复扫描")
            self.debug_label.setText("调试信息: 扫描已暂停")
        else:
            # 恢复扫描
            self.scanning_enabled = True
            self.timer.start()
            self.scan_button.setText("暂停扫描")
            self.debug_label.setText("调试信息: 扫描已恢复")

    def get_device_details(self, device):
        """收集设备详细信息"""
        try:
            details = []
            details.append(f"设备名称: {device.name or '未知'}")
            details.append(f"MAC地址: {device.address}")
            details.append(f"信号强度: {device.rssi}dBm")
            
            if device.metadata.get("manufacturer_data"):
                details.append("\n制造商数据:")
                for company_id, data in device.metadata["manufacturer_data"].items():
                    details.append(f"公司ID: {company_id:04x}")
                    details.append("解析数据:")
                    details.append(parse_manufacturer_data(data))
            
            if device.metadata.get("uuids"):
                details.append("\n服务UUID:")
                for uuid in device.metadata["uuids"]:
                    details.append(uuid)
            
            return "\n".join(details)
        except Exception as e:
            logger.error(f"收集设备详情时出错: {e}")
            return f"获取设备详情时出错: {str(e)}"

if __name__ == "__main__":
    app = QApplication([])
    window = FreeBudsWindow()
    window.show()
    app.exec()
