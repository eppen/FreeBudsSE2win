import asyncio
import logging
import struct
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient, BleakError
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QMessageBox, QPushButton, QHBoxLayout, QScrollArea, QFrame, QGroupBox, QCheckBox
from PyQt6.QtCore import QTimer, QThread, Qt, pyqtSignal
from popup import BatteryPopup
from huawei_spp import HuaweiSPPClient
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

def extract_battery_info(manufacturer_data):
    """
    尝试从制造商数据中提取电量信息 (L, R, Case)
    返回 (left, right, case) 或者 None
    """
    for cid, data in manufacturer_data.items():
        # 常见华为/FreeBuds ID: 0x0156 (Huawei), 0x025D (Huawei HiLink?), 0x004C (Apple spoof)
        # 尝试多种解析策略
        data_hex = data.hex()
        
        # 策略1: 常见FreeBuds位置 (假设)
        # 许多华为耳机在 0x0156 或 0x004C 下广播
        # 如果数据长度足够，尝试读取常见偏移量
        # 这里使用一种通用查找: 寻找连续的电量值
        
        # 调试: 如果想看原始数据，可以在日志输出
        # logger.debug(f"Hex ({cid:04x}): {data_hex}")
        
        # 假设格式: ... L R Case ... (具体偏移需抓包确认，这里使用假设或基于开源库)
        # 根据 FreeBuds-Lite-Battery-Level 等项目
        # 可能是 偏移量 7, 8, 9 或类似的
        # 这是一个简化的假设，用户可能需要根据实际日志调整
        if len(data) >= 10:
             # 仅作为示例，实际需要根据设备具体协议
             # FreeBuds SE 2 可能类似
             pass
             
    return None

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

class SPPWorker(QThread):
    status_changed = pyqtSignal(str)
    battery_received = pyqtSignal(int, int, int)
    
    def __init__(self, address):
        super().__init__()
        self.address = address
        self.client = HuaweiSPPClient(address)
        self.command_queue = [] # List of (cmd, val)
        self.running = True
        
    def run(self):
        self.status_changed.emit("正在尝试建立SPP连接...")
        if self.client.connect():
            self.status_changed.emit("SPP已连接")
        else:
            self.status_changed.emit("SPP连接失败. 请确保设备已配对")
            return

        while self.running:
            if self.command_queue:
                cmd, val = self.command_queue.pop(0)
                try:
                    if cmd == 'get_battery':
                        self.status_changed.emit("正在读取电量...")
                        res = self.client.get_battery()
                        if res and 'left' in res:
                            self.battery_received.emit(res['left'], res['right'], res['case'])
                            self.status_changed.emit("已主动更新电量")
                        else:
                            self.status_changed.emit("读取电量失败 (无响应)")
                            
                    elif cmd == 'set_low_latency':
                        self.client.set_low_latency(val)
                        self.status_changed.emit(f"低延迟模式已{'开启' if val else '关闭'}")
                        
                except Exception as e:
                    self.status_changed.emit(f"命令执行失败: {e}")
            
            time.sleep(0.1)
            
        self.client.disconnect()
        self.status_changed.emit("SPP已断开")

    def queue_command(self, cmd, val=None):
        self.command_queue.append((cmd, val))

    def stop(self):
        self.running = False


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
    battery_signal = pyqtSignal(int, int, int)

    def __init__(self):
        try:
            super().__init__()
            self.battery_signal.connect(self.update_battery_popup)
            self.popup = BatteryPopup() 
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

            # SPP Controls
            spp_group = QGroupBox("主动控制 (需在Windows已配对)")
            spp_layout = QVBoxLayout()
            
            spp_btn_layout = QHBoxLayout()
            self.btn_spp_connect = QPushButton("连接 (SPP)")
            self.btn_spp_connect.clicked.connect(self.toggle_spp_connection)
            self.btn_spp_refresh_bat = QPushButton("读取电量")
            self.btn_spp_refresh_bat.clicked.connect(self.spp_refresh_battery)
            self.btn_spp_refresh_bat.setEnabled(False)
            
            spp_btn_layout.addWidget(self.btn_spp_connect)
            spp_btn_layout.addWidget(self.btn_spp_refresh_bat)
            
            self.chk_low_latency = QCheckBox("低延迟 (游戏) 模式")
            self.chk_low_latency.setEnabled(False)
            self.chk_low_latency.clicked.connect(self.spp_set_low_latency)
            
            spp_layout.addLayout(spp_btn_layout)
            spp_layout.addWidget(self.chk_low_latency)
            spp_group.setLayout(spp_layout)
            layout.addWidget(spp_group)

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
        # self.timer.start(3000)  # 不再需要定期触发，因为我们改用了continuous scanning
        # 仅触发一次启动
        QTimer.singleShot(1000, self.start_scan)

        # 初始化SPP Worker
        self.spp_worker = None

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

    def toggle_spp_connection(self):
        if self.spp_worker and self.spp_worker.running:
            # Disconnect
            self.spp_worker.stop()
            self.spp_worker.wait()
            self.spp_worker = None
            self.btn_spp_connect.setText("连接 (SPP)")
            self.btn_spp_refresh_bat.setEnabled(False)
            self.chk_low_latency.setEnabled(False)
            self.debug_label.setText("调试信息: SPP已断开")
        else:
            # Connect
            # Try to use discovered address or first default
            target_addr = None
            
            # 1. Try last BLE discovered specific target
            # Not easy to get from here unless we stored it. 
            # We check found_devices
            for d in self.found_devices:
                 norm_addr = normalize_address(d.address)
                 if any(target in norm_addr for target in [normalize_address(a)[2] for a in DEVICE_ADDRESSES]):
                     target_addr = d.address
                     break
            
            # 2. Fallback to first configured address
            if not target_addr:
                target_addr = DEVICE_ADDRESSES[0]
            
            self.spp_worker = SPPWorker(target_addr)
            self.spp_worker.status_changed.connect(lambda s: self.debug_label.setText(f"SPP: {s}"))
            self.spp_worker.status_changed.connect(self.check_spp_connection_ui)
            self.spp_worker.battery_received.connect(self.update_battery_popup)
            self.spp_worker.start()
            self.btn_spp_connect.setText("断开 (SPP)")

    def check_spp_connection_ui(self, status_msg):
        if "已连接" in status_msg:
            self.btn_spp_refresh_bat.setEnabled(True)
            self.chk_low_latency.setEnabled(True)
            
            # SPP连接成功后，自动暂停BLE扫描以节省资源
            if self.scanning_enabled:
                self.scanning_enabled = False
                self.scan_button.setText("恢复扫描")
                logger.debug("SPP已连接，自动暂停BLE扫描")

        elif "失败" in status_msg or "断开" in status_msg:
            self.btn_spp_refresh_bat.setEnabled(False)
            self.chk_low_latency.setEnabled(False)
            if self.spp_worker and not self.spp_worker.running:
                 self.btn_spp_connect.setText("连接 (SPP)")

    def spp_refresh_battery(self):
        if self.spp_worker:
            self.spp_worker.queue_command('get_battery')

    def spp_set_low_latency(self):
        if self.spp_worker:
            enabled = self.chk_low_latency.isChecked()
            self.spp_worker.queue_command('set_low_latency', enabled)

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

    def update_battery_popup(self, left, right, case):
        if self.popup:
            self.popup.update_batteries(left, right, case)
            if left <= 100: self.left_battery_label.setText(f"左耳机电量: {left}%")
            if right <= 100: self.right_battery_label.setText(f"右耳机电量: {right}%")
            if case <= 100: self.case_battery_label.setText(f"充电盒电量: {case}%")
            
            self.status_label.setText(f"状态: 监测到设备广播 (L:{left}% R:{right}% Case:{case}%)")

    def parse_battery_from_adv(self, advertisement_data):
        """解析广播数据中的电量信息"""
        if not advertisement_data.manufacturer_data:
            return None
            
        for cid, data in advertisement_data.manufacturer_data.items():
            # 简单启发式解析：查找看起来像电量的3个字节 (L, R, Case)
            # 这里的逻辑是尝试找到符合 FreeBuds 特征的数据
            # 1. 长度检查
            if len(data) < 7: continue 
            
            # 2. 尝试常见偏移量 (例如很多华为设备在偏移量 2,3,4 或 7,8,9 等)
            # 由于没有确切文档，我们扫描数据寻找可能的电量组合
            # 电量通常为 0-100，或者 255 (未知/未放入)
            
            # 使用滑动窗口查找
            data_bytes = list(data)
            for i in range(len(data_bytes) - 2):
                l, r, c = data_bytes[i], data_bytes[i+1], data_bytes[i+2]
                
                # 验证是否为合理电量值 (0-100 或 255)
                valid_l = (0 <= l <= 100) or l == 255
                valid_r = (0 <= r <= 100) or r == 255
                valid_c = (0 <= c <= 100) or c == 255
                
                # 必须至少有一个有效且非255的值 (避免误判)
                has_value = (0 <= l <= 100) or (0 <= r <= 100) or (0 <= c <= 100)
                
                if valid_l and valid_r and valid_c and has_value:
                    if cid == 0x0156 or cid == 0x025D: # 优先匹配华为ID
                        return (l, r, c)
                        
            # 如果仅仅是 0x0156 且没找到明显连续字节，尝试 0x4C (Apple) 格式
            # (有些 FreeBuds 伪装成 AirPods)
            if cid == 0x004C:
                # Apple battery format usually: length 27 ish
                pass
                
        return None

    @catch_exception
    async def scan_devices(self):
        if hasattr(self, '_is_scanning') and self._is_scanning:
            return

        self._is_scanning = True
        logger.debug("启动持续扫描模式...")
        self.debug_label.setText("调试信息: 正在监听广播数据(Pop-up模式)...")
        self.scan_time_label.setText("扫描模式: 持续后台监听")

        def detection_callback(device, advertisement_data):
            try:
                # 检查是否为目标设备
                is_target = False
                
                # 地址检查
                if device.address:
                    norm_addr = normalize_address(device.address)
                    if any(target in norm_addr for target in [normalize_address(a)[2] for a in DEVICE_ADDRESSES]):
                        is_target = True
                
                # 名称检查
                if not is_target and device.name:
                    if any(n in device.name for n in DEVICE_NAMES):
                        is_target = True
                
                if is_target:
                    # 尝试解析电量
                    bat_info = self.parse_battery_from_adv(advertisement_data)
                    if bat_info:
                        l, r, c = bat_info
                        self.battery_signal.emit(l, r, c)
                        logger.debug(f"收到电量广播: L={l} R={r} C={c}")
                        
                        # 可以在这里更新UI显示的"上次活动时间"等
            except Exception as e:
                logger.error(f"回调处理错误: {e}")

        try:
            self.scanner = BleakScanner(detection_callback=detection_callback)
            await self.scanner.start()
            
            while self.scanning_enabled:
                await asyncio.sleep(1)
                
            await self.scanner.stop()
            
        except Exception as e:
            logger.error(f"扫描异常: {e}")
            self.debug_label.setText(f"扫描出错: {e}")
        finally:
            if hasattr(self, 'scanner'):
                try:
                    await self.scanner.stop()
                except:
                    pass
            self._is_scanning = False


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
                logger.debug("扫描已禁用，重新启用")
                self.scanning_enabled = True
            
            # 如果已经在扫描中，不再重复启动
            if hasattr(self, '_is_scanning') and self._is_scanning:
                logger.debug("扫描已在运行中")
                return

            if not self.async_thread or not self.async_thread.loop:
                logger.error("异步线程或事件循环未初始化")
                self.init_other_components()
                return
            
            asyncio.run_coroutine_threadsafe(self.scan_devices(), self.async_thread.loop)
        except Exception as e:
            logger.exception("启动扫描时出错")
            self.debug_label.setText(f"调试信息: 启动扫描出错 - {str(e)}")


    def resume_scanning(self):
        """恢复扫描"""
        try:
            # 恢复扫描
            logger.debug("恢复扫描")
            self.scanning_enabled = True
            self.start_scan()
            self.status_label.setText("状态: 扫描已恢复")
            self.scan_button.setText("暂停扫描")
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
            self.debug_label.setText("调试信息: 正在停止扫描...")
        else:
            # 恢复扫描
            self.scanning_enabled = True
            # self.timer.start() 
            self.start_scan()
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
