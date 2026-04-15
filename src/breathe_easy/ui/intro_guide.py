from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt

class IntroGuideWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #050D26; color: #E0E5FF;")
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)
        
        title = QLabel("BreatheEasy: Zen Journey")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #FFF; margin-bottom: 20px;")
        title.setAlignment(Qt.AlignCenter)
        
        intro = QLabel(
            "Welcome to BreatheEasy. This biofeedback art experience maps your real-time heart rate "
            "variability and respiration into immersive bioluminescent waves and ocean sounds.\n\n"
            "As you breathe slowly and deeply, the system calculates your Respiratory Sinus Arrhythmia (Coherence).\n"
            "High Coherence transitions chaotic noise into smooth, rhythmic, and brightly glowing visual pathways."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 16px; margin: 0 40px; text-align: center;")
        intro.setAlignment(Qt.AlignCenter)
        
        
        guide = QLabel(
            "• Inhale: The glowing waves expand outwards and pitch rises.\n"
            "• Exhale: The waves contract inwards as you release your breath.\n"
            "• Coherence: The base ocean blue begins to glow and mix into vibrant colors as your parasympathetic state dominates.\n\n"
            "Use the controls at the bottom to connect your Polar H10 chest strap, or use the Mock sensor for the Hackathon demo."
        )
        guide.setStyleSheet("font-size: 15px; margin: 40px;")
        guide.setWordWrap(True)
        
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(guide)
        
        # Spacer
        layout.addStretch()
