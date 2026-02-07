from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QProgressBar
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

class BatteryPopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BatteryPopup")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) # 取消透明设置，避免部分系统显示异常
        
        # Style
        self.setStyleSheet("""
            QWidget#BatteryPopup {
                background-color: #f7f7f7;
                border: 1px solid #d0d0d0;
            }
            QLabel {
                color: #333;
                background: transparent;
            }
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 5px;
                text-align: center;
                background-color: #eee;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }
        """)
        
        # Layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        # Title
        self.title_label = QLabel("HUAWEI FreeBuds SE 2")
        self.title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.title_label)
        
        # Battery Info Layout
        info_layout = QHBoxLayout()
        
        # Left
        self.l_label = QLabel("L")
        self.l_bar = QProgressBar()
        self.l_bar.setRange(0, 100)
        self.l_bar.setOrientation(Qt.Orientation.Vertical)
        self.l_bar.setFixedSize(20, 60)
        self.l_text = QLabel("--%")
        
        l_layout = QVBoxLayout()
        l_layout.addWidget(self.l_label, alignment=Qt.AlignmentFlag.AlignCenter)
        l_layout.addWidget(self.l_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        l_layout.addWidget(self.l_text, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Case
        self.c_label = QLabel("Case")
        self.c_bar = QProgressBar()
        self.c_bar.setRange(0, 100)
        self.c_bar.setOrientation(Qt.Orientation.Vertical)
        self.c_bar.setFixedSize(20, 60)
        self.c_text = QLabel("--%")

        c_layout = QVBoxLayout()
        c_layout.addWidget(self.c_label, alignment=Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.c_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.c_text, alignment=Qt.AlignmentFlag.AlignCenter)

        # Right
        self.r_label = QLabel("R")
        self.r_bar = QProgressBar()
        self.r_bar.setRange(0, 100)
        self.r_bar.setOrientation(Qt.Orientation.Vertical)
        self.r_bar.setFixedSize(20, 60)
        self.r_text = QLabel("--%")

        r_layout = QVBoxLayout()
        r_layout.addWidget(self.r_label, alignment=Qt.AlignmentFlag.AlignCenter)
        r_layout.addWidget(self.r_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        r_layout.addWidget(self.r_text, alignment=Qt.AlignmentFlag.AlignCenter)
        
        info_layout.addLayout(l_layout)
        info_layout.addLayout(c_layout)
        info_layout.addLayout(r_layout)
        
        main_layout.addLayout(info_layout)
        
        self.resize(300, 200)
        
        # Auto hide timer
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.hide)
        self.hide_timer.setSingleShot(True)

    def update_batteries(self, left, right, case):
        self.l_bar.setValue(left)
        self.l_text.setText(f"{left}%")
        
        self.r_bar.setValue(right)
        self.r_text.setText(f"{right}%")
        
        self.c_bar.setValue(case)
        self.c_text.setText(f"{case}%")
        
        # Show window
        self.show()
        # Move to bottom right or center
        # For now, let's just show it. Ideally, center of screen or bottom right.
        
        # Reset hide timer (e.g., 5 seconds)
        self.hide_timer.start(5000)
