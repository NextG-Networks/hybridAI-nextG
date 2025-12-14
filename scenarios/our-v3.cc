/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/applications-module.h"
#include "ns3/point-to-point-helper.h"
#include "ns3/mmwave-helper.h"
#include "ns3/mmwave-point-to-point-epc-helper.h"
#include "ns3/mmwave-ue-net-device.h"
#include "ns3/mmwave-enb-net-device.h"
#include "ns3/v4ping-helper.h"
#include "ns3/v4ping.h"
#include "ns3/packet-sink-helper.h"
#include "ns3/packet-sink.h"
#include "ns3/netanim-module.h"
#include "ns3/waypoint-mobility-model.h"
#include "ns3/buildings-module.h"
#include "ns3/buildings-helper.h"

#include "ns3/mmwave-component-carrier-enb.h"
#include "ns3/mmwave-flex-tti-mac-scheduler.h"
#include "ns3/on-off-application.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <cmath>
#include <ctime>
#include <vector>

using namespace ns3;
using namespace mmwave;
namespace fs = std::filesystem;

NS_LOG_COMPONENT_DEFINE("MVS_Mmwave_1gNB_1UE_v3");

// ---------------- Runtime flags ----------------
static GlobalValue g_simTime("simTime", "Simulation time (s)",
  DoubleValue(3599.0), MakeDoubleChecker<double>(1.0, 3600.0));
static GlobalValue g_outDir("outDir", "Output directory",
  StringValue("out/logs"), MakeStringChecker());

// (kept from your file)
static GlobalValue q_useSemaphores ("useSemaphores","If true, enables the use of semaphores for external environment control",
                                    BooleanValue(false), MakeBooleanChecker());
static GlobalValue g_controlFileName ("controlFileName","The path to the control file (can be absolute)",
                                      StringValue(""), MakeStringChecker());
static GlobalValue g_e2lteEnabled ("e2lteEnabled","If true, send LTE E2 reports",
                                   BooleanValue(true), MakeBooleanChecker());
static GlobalValue g_e2nrEnabled ("e2nrEnabled","If true, send NR E2 reports",
                                  BooleanValue(true), MakeBooleanChecker());
static GlobalValue g_e2du ("e2du","If true, send DU reports",
                           BooleanValue(true), MakeBooleanChecker());
static GlobalValue g_e2cuUp ("e2cuUp","If true, send CU-UP reports",
                             BooleanValue(true), MakeBooleanChecker());
static GlobalValue g_e2cuCp ("e2cuCp","If true, send CU-CP reports",
                             BooleanValue(true), MakeBooleanChecker());
static GlobalValue g_indicationPeriodicity ("indicationPeriodicity","E2 Indication Periodicity (s)", 
                                            DoubleValue(0.1), MakeDoubleChecker<double>(0.01, 2.0));
static GlobalValue g_e2TermIp ("e2TermIp","RIC E2 termination IP",
                               StringValue("10.0.2.10"), MakeStringChecker());
static GlobalValue g_enableE2FileLogging ("enableE2FileLogging","Offline file logging instead of connecting to RIC",
                                          BooleanValue(false), MakeBooleanChecker());
static GlobalValue g_reducedPmValues ("reducedPmValues", "If true, use a subset of the pm containers",
                                      BooleanValue(false), MakeBooleanChecker());

// ---------------- Traffic Randomization ----------------
void ChangeTrafficProfile(Ptr<OnOffApplication> app, bool highLoad) {
  // Toggle between High (100Mbps) and Low (10Mbps) load
  std::string rate = highLoad ? "100Mbps" : "10Mbps";
  app->SetAttribute("DataRate", StringValue(rate));
  NS_LOG_UNCOND("Traffic Profile Changed: " << (highLoad ? "HIGH" : "LOW") << " (" << rate << ")");
  
  // Schedule next change (random time between 5s and 15s)
  Ptr<UniformRandomVariable> var = CreateObject<UniformRandomVariable>();
  double nextTime = var->GetValue(5.0, 15.0);
  Simulator::Schedule(Seconds(nextTime), &ChangeTrafficProfile, app, !highLoad);
}



 
// ---------------- Timeseries sampler (drop-in)// Global variables
struct GlobalState {
  double lastT = 0.0;
  uint64_t lastBytes = 0;
  double ewma = 0.0;
  bool seenPing = false;
  double lastPingMs = 0.0;
} gS;

AnimationInterface *g_anim = nullptr; // Global pointer for NetAnim updates

static void PingRttCallback(Time rtt) {
  gS.lastPingMs = rtt.GetMilliSeconds();
  gS.seenPing   = true;
}

static void SampleAll(const NodeContainer &ueNodes,
                      const NetDeviceContainer &ueDevs,
                      Ptr<Node> gnbNode,
                      double covRadius,
                      Ptr<PacketSink> sink0,
                      double periodSec)
{
  static std::ofstream f;
  static bool headerDone = false;

  if (!headerDone) {
    f.open("sim_timeseries_v3.csv", std::ios::out | std::ios::trunc);
    f << std::fixed << std::setprecision(6);
    f << "time_s";
    for (uint32_t i = 0; i < ueNodes.GetN(); ++i) {
      f << ",ue" << i << "_imsi"
        << ",ue" << i << "_x"
        << ",ue" << i << "_y"
        << ",ue" << i << "_z"
        << ",ue" << i << "_dist_to_gnb_m"
        << ",ue" << i << "_inside";
    }
    f << ",throughput_ue0_mbps"
      << ",throughput_ue0_ewma"
      << ",ping_ms"
      << ",mcs_dl"           // Add MCS column
      << ",mcs_ul"           // Add UL MCS column
      << ",fixed_mcs_dl"     // Add fixed MCS flag
      << "\n";
    headerDone = true;
  }

  const double now = Simulator::Now().GetSeconds();
  Vector gp = gnbNode->GetObject<MobilityModel>()->GetPosition();
  f << now;

  for (uint32_t i = 0; i < ueNodes.GetN(); ++i) {
    Vector p = ueNodes.Get(i)->GetObject<MobilityModel>()->GetPosition();
    const double dx = p.x - gp.x, dy = p.y - gp.y, dz = p.z - gp.z;
    const double dist = std::sqrt(dx*dx + dy*dy + dz*dz);
    const int inside = (dist <= covRadius ? 1 : 0);
    uint64_t imsi = ueDevs.Get(i)->GetObject<MmWaveUeNetDevice>()->GetImsi();

    f << "," << imsi
      << "," << p.x
      << "," << p.y
      << "," << p.z
      << "," << dist
      << "," << inside;
  }

  double mbps = 0.0;
  if (sink0) {
    uint64_t bytes = sink0->GetTotalRx();
    double dt = now - gS.lastT;
    if (gS.lastT > 0.0 && dt > 0.0) {
      mbps = 8.0 * (bytes - gS.lastBytes) / dt / 1e6;
    }
    gS.lastBytes = bytes;
    gS.lastT = now;
  }

  const double tau = 1.0; // EWMA time constant (s)
  const double alpha = 1.0 - std::exp(-(periodSec / tau));
  gS.ewma = alpha * mbps + (1.0 - alpha) * gS.ewma;

  double pingMs = gS.seenPing ? gS.lastPingMs : 0.0;



  // Get MCS values from scheduler
  uint8_t mcsDl = 255;  // 255 = adaptive/not fixed
  uint8_t mcsUl = 255;
  bool fixedMcsDl = false;
  
  Ptr<mmwave::MmWaveEnbNetDevice> enbDev = gnbNode->GetDevice(0)->GetObject<mmwave::MmWaveEnbNetDevice>();
  if (enbDev) {
    // Access scheduler through component carrier
    std::map<uint8_t, Ptr<mmwave::MmWaveComponentCarrier>> ccMap = enbDev->GetCcMap();
    if (!ccMap.empty()) {
      Ptr<mmwave::MmWaveComponentCarrierEnb> cc = 
          DynamicCast<mmwave::MmWaveComponentCarrierEnb>(ccMap.at(0));
      if (cc) {
        Ptr<mmwave::MmWaveMacScheduler> sched = cc->GetMacScheduler();
        if (sched) {
          Ptr<mmwave::MmWaveFlexTtiMacScheduler> flexSched = 
              DynamicCast<mmwave::MmWaveFlexTtiMacScheduler>(sched);
          if (flexSched) {
            mcsDl = flexSched->GetCurrentMcsDl();
            mcsUl = flexSched->GetCurrentMcsUl();
            fixedMcsDl = flexSched->IsFixedMcsDl();
          }
        }
      }
    }
  }

  f << "," << mbps
    << "," << gS.ewma
    << "," << pingMs
    << "," << static_cast<int>(mcsDl)    // Add MCS DL value
    << "," << static_cast<int>(mcsUl)    // Add MCS UL value
    << "," << (fixedMcsDl ? 1 : 0)       // Add fixed MCS flag
    << "\n";
  f.flush();

  // Update NetAnim description with throughput
  if (g_anim) {
      std::ostringstream oss;
      oss << "UE0 (" << std::fixed << std::setprecision(1) << mbps << " Mbps)";
      g_anim->UpdateNodeDescription(ueNodes.Get(0), oss.str());
  }

  Simulator::Schedule(Seconds(periodSec), &SampleAll,
                      ueNodes, ueDevs, gnbNode, covRadius, sink0, periodSec);
}

// ---------------- Dynamic MCS Logic ----------------
static void ChangeMcs(Ptr<Node> gnb, int mcs)
{
  Ptr<mmwave::MmWaveEnbNetDevice> enbDev = gnb->GetDevice(0)->GetObject<mmwave::MmWaveEnbNetDevice>();
  if (!enbDev) return;

  std::map<uint8_t, Ptr<mmwave::MmWaveComponentCarrier>> ccMap = enbDev->GetCcMap();
  if (ccMap.empty()) return;

  Ptr<mmwave::MmWaveComponentCarrierEnb> cc = DynamicCast<mmwave::MmWaveComponentCarrierEnb>(ccMap.at(0));
  if (!cc) return;

  Ptr<mmwave::MmWaveMacScheduler> sched = cc->GetMacScheduler();
  if (!sched) return;

  Ptr<mmwave::MmWaveFlexTtiMacScheduler> flexSched = DynamicCast<mmwave::MmWaveFlexTtiMacScheduler>(sched);
  if (flexSched) {
    if (mcs >= 0) {
      flexSched->SetAttribute("FixedMcsDl", BooleanValue(true));
      flexSched->SetAttribute("McsDefaultDl", UintegerValue(mcs));
      flexSched->SetAttribute("FixedMcsUl", BooleanValue(true));
      flexSched->SetAttribute("McsDefaultUl", UintegerValue(mcs));
      NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [Scenario] Setting Fixed MCS to " << mcs);
      std::cerr << "  → MCS change applied: Fixed MCS=" << mcs << " (DL and UL)" << std::endl;
    } else {
      flexSched->SetAttribute("FixedMcsDl", BooleanValue(false));
      flexSched->SetAttribute("FixedMcsUl", BooleanValue(false));
      NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [Scenario] Restoring Adaptive MCS");
      std::cerr << "  → MCS change applied: Adaptive MCS restored (DL and UL)" << std::endl;
    }
  }
}

static int GetCurrentMcs(Ptr<Node> gnb)
{
  Ptr<mmwave::MmWaveEnbNetDevice> enbDev = gnb->GetDevice(0)->GetObject<mmwave::MmWaveEnbNetDevice>();
  if (!enbDev) return -1;
  std::map<uint8_t, Ptr<mmwave::MmWaveComponentCarrier>> ccMap = enbDev->GetCcMap();
  if (ccMap.empty()) return -1;
  Ptr<mmwave::MmWaveComponentCarrierEnb> cc = DynamicCast<mmwave::MmWaveComponentCarrierEnb>(ccMap.at(0));
  if (!cc) return -1;
  Ptr<mmwave::MmWaveMacScheduler> sched = cc->GetMacScheduler();
  if (!sched) return -1;
  Ptr<mmwave::MmWaveFlexTtiMacScheduler> flexSched = DynamicCast<mmwave::MmWaveFlexTtiMacScheduler>(sched);
  if (flexSched) {
    return flexSched->GetCurrentMcsDl();
  }
  return -1;
}

static void ScheduleNextMcsEvent(Ptr<Node> gnb, bool nextIsLow)
{
  if (nextIsLow) {
    int targetMcs = 4 + (rand() % 6); 
    ChangeMcs(gnb, targetMcs);
    double duration = 60.0;
    std::cerr << "\n"
              << "╔════════════════════════════════════════════════════════════╗\n"
              << "║  [EVENT] MCS DEGRADATION TRIGGERED                        ║\n"
              << "╠════════════════════════════════════════════════════════════╣\n"
              << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
              << "║  Action: Setting LOW MCS (4-9 range)                      ║\n"
              << "║  Target MCS: " << std::setw(42) << targetMcs << " ║\n"
              << "║  Duration: " << std::setw(45) << duration << "s ║\n"
              << "║  Impact: System performance will degrade                  ║\n"
              << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
    NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] MCS DEGRADATION - Setting LOW MCS=" << targetMcs << " for " << duration << "s");
    Simulator::Schedule(Seconds(duration), [gnb, targetMcs]() {
      int currentMcs = GetCurrentMcs(gnb);
      if (currentMcs != -1 && currentMcs != targetMcs) {
        std::cerr << "\n"
                  << "╔════════════════════════════════════════════════════════════╗\n"
                  << "║  [EVENT] AI INTERVENTION DETECTED                         ║\n"
                  << "╠════════════════════════════════════════════════════════════╣\n"
                  << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                  << "║  Expected MCS: " << std::setw(40) << targetMcs << " ║\n"
                  << "║  Actual MCS: " << std::setw(43) << currentMcs << " ║\n"
                  << "║  Action: Extending event window by 5s                    ║\n"
                  << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
        NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] AI intervention detected (MCS=" << currentMcs << " != " << targetMcs << "). Extending window by 5s.");
        Simulator::Schedule(Seconds(5.0), [gnb]() { ScheduleNextMcsEvent(gnb, false); });
      } else {
        ScheduleNextMcsEvent(gnb, false);
      }
    });
  } else {
    int targetMcs = 1 + (rand() % 28);
    ChangeMcs(gnb, targetMcs);
    double duration = 60.0; 
    std::cerr << "\n"
              << "╔════════════════════════════════════════════════════════════╗\n"
              << "║  [EVENT] MCS RANDOMIZATION TRIGGERED                      ║\n"
              << "╠════════════════════════════════════════════════════════════╣\n"
              << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
              << "║  Action: Setting RANDOM MCS (1-28 range)                  ║\n"
              << "║  Target MCS: " << std::setw(42) << targetMcs << " ║\n"
              << "║  Duration: " << std::setw(45) << duration << "s ║\n"
              << "║  Impact: Unpredictable system performance                 ║\n"
              << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
    NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] MCS RANDOMIZATION - Setting RANDOM MCS=" << targetMcs << " for " << duration << "s");
    Simulator::Schedule(Seconds(duration), [gnb, targetMcs]() {
      int currentMcs = GetCurrentMcs(gnb);
      if (currentMcs != -1 && currentMcs != targetMcs) {
        std::cerr << "\n"
                  << "╔════════════════════════════════════════════════════════════╗\n"
                  << "║  [EVENT] AI INTERVENTION DETECTED                         ║\n"
                  << "╠════════════════════════════════════════════════════════════╣\n"
                  << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                  << "║  Expected MCS: " << std::setw(40) << targetMcs << " ║\n"
                  << "║  Actual MCS: " << std::setw(43) << currentMcs << " ║\n"
                  << "║  Action: Extending event window by 5s                    ║\n"
                  << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
        NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] AI intervention detected (MCS=" << currentMcs << " != " << targetMcs << "). Extending window by 5s.");
        Simulator::Schedule(Seconds(5.0), [gnb]() { ScheduleNextMcsEvent(gnb, true); });
      } else {
        ScheduleNextMcsEvent(gnb, true);
      }
    });
  }
}

// ---------------- NEW RANDOM EVENTS ----------------

static void RandomBlockageEvent(NodeContainer ues, Ptr<Node> gnb)
{
    // Randomly select a UE
    uint32_t ueIdx = rand() % ues.GetN();
    Ptr<Node> ue = ues.Get(ueIdx);
    
    // Simulate blockage by increasing Noise Figure of the UE receiver
    // This effectively lowers the SINR
    Ptr<mmwave::MmWaveUeNetDevice> ueDev = ue->GetDevice(0)->GetObject<mmwave::MmWaveUeNetDevice>();
    if (ueDev) {
        Ptr<mmwave::MmWaveUePhy> phy = ueDev->GetPhy();
        if (phy) {
            double originalNf = phy->GetNoiseFigure();
            double blockageNf = originalNf + 30.0; // +30dB noise figure = severe blockage
            
            phy->SetNoiseFigure(blockageNf);
            uint64_t imsi = ueDev->GetImsi();
            std::cerr << "\n"
                      << "╔════════════════════════════════════════════════════════════╗\n"
                      << "║  [EVENT] RANDOM BLOCKAGE TRIGGERED                       ║\n"
                      << "╠════════════════════════════════════════════════════════════╣\n"
                      << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                      << "║  Affected UE: " << std::setw(42) << imsi << " ║\n"
                      << "║  UE Index: " << std::setw(45) << ueIdx << " ║\n"
                      << "║  Original Noise Figure: " << std::setprecision(1) << std::setw(33) << originalNf << " dB ║\n"
                      << "║  Blockage Noise Figure: " << std::setw(33) << blockageNf << " dB ║\n"
                      << "║  Impact: +30dB noise = severe signal degradation         ║\n"
                      << "║  Duration: " << std::setprecision(0) << std::setw(45) << "5.0s ║\n"
                      << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
            NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] Random Blockage triggered for UE " << imsi << " (NF increased by 30dB from " << originalNf << "dB to " << blockageNf << "dB)");
            
            // Restore after 5 seconds
            Simulator::Schedule(Seconds(5.0), [phy, originalNf, ueDev, imsi, ueIdx]() {
                phy->SetNoiseFigure(originalNf);
                std::cerr << "\n"
                          << "╔════════════════════════════════════════════════════════════╗\n"
                          << "║  [EVENT] RANDOM BLOCKAGE ENDED                           ║\n"
                          << "╠════════════════════════════════════════════════════════════╣\n"
                          << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                          << "║  Affected UE: " << std::setw(42) << imsi << " ║\n"
                          << "║  UE Index: " << std::setw(45) << ueIdx << " ║\n"
                          << "║  Noise Figure restored to: " << std::setprecision(1) << std::setw(30) << originalNf << " dB ║\n"
                          << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
                NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] Random Blockage ended for UE " << imsi << " (NF restored to " << originalNf << "dB)");
            });
        }
    }
    
    // Schedule next blockage event (random time between 15-30s)
    double nextTime = 15.0 + (rand() % 15);
    Simulator::Schedule(Seconds(nextTime), &RandomBlockageEvent, ues, gnb);
}

static void TrafficSpikeEvent(NodeContainer remoteHosts)
{
    // Randomly select a Remote Host (which generates traffic for a UE)
    uint32_t rhIdx = rand() % remoteHosts.GetN();
    Ptr<Node> rh = remoteHosts.Get(rhIdx);
    
    // Find the OnOffApplication
    Ptr<OnOffApplication> onOffApp = nullptr;
    for (uint32_t i = 0; i < rh->GetNApplications(); ++i) {
        onOffApp = DynamicCast<OnOffApplication>(rh->GetApplication(i));
        if (onOffApp) break;
    }
    
    if (onOffApp) {
        // Increase data rate significantly
        DataRate originalRate("50Mbps"); // Assuming default
        DataRate spikeRate("500Mbps");   // 10x spike
        
        onOffApp->SetAttribute("DataRate", DataRateValue(spikeRate));
        std::cerr << "\n"
                  << "╔════════════════════════════════════════════════════════════╗\n"
                  << "║  [EVENT] TRAFFIC SPIKE TRIGGERED                          ║\n"
                  << "╠════════════════════════════════════════════════════════════╣\n"
                  << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                  << "║  Remote Host Index: " << std::setw(38) << rhIdx << " ║\n"
                  << "║  Original Data Rate: " << std::setw(38) << "50 Mbps ║\n"
                  << "║  Spike Data Rate: " << std::setw(40) << "500 Mbps ║\n"
                  << "║  Increase: " << std::setw(47) << "10x (1000%) ║\n"
                  << "║  Impact: Network congestion, higher latency              ║\n"
                  << "║  Duration: " << std::setw(45) << "5.0s ║\n"
                  << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
        NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] Traffic Spike triggered for RH " << rhIdx << " (50Mbps -> 500Mbps, 10x increase)");
        
        // Restore after 5 seconds
        Simulator::Schedule(Seconds(5.0), [onOffApp, originalRate, rhIdx]() {
            onOffApp->SetAttribute("DataRate", DataRateValue(originalRate));
            std::cerr << "\n"
                      << "╔════════════════════════════════════════════════════════════╗\n"
                      << "║  [EVENT] TRAFFIC SPIKE ENDED                              ║\n"
                      << "╠════════════════════════════════════════════════════════════╣\n"
                      << "║  Time: " << std::fixed << std::setprecision(2) << std::setw(48) << Simulator::Now().GetSeconds() << "s ║\n"
                      << "║  Remote Host Index: " << std::setw(38) << rhIdx << " ║\n"
                      << "║  Data Rate restored to: " << std::setw(35) << "50 Mbps ║\n"
                      << "╚════════════════════════════════════════════════════════════╝\n" << std::endl;
            NS_LOG_UNCOND(Simulator::Now().GetSeconds() << "s: [EVENT] Traffic Spike ended for RH " << rhIdx << " (restored to 50Mbps)");
        });
    }
    
    // Schedule next spike event (random time between 20-40s)
    double nextTime = 20.0 + (rand() % 20);
    Simulator::Schedule(Seconds(nextTime), &TrafficSpikeEvent, remoteHosts);
}


// ---------------- main ----------------
int main (int argc, char** argv)
{
  CommandLine cmd;
  int rngSeed = 0;
  cmd.AddValue("rngSeed", "Seed for random number generator (default 0 = random)", rngSeed);
  cmd.Parse(argc, argv);

  if (rngSeed == 0) {
    srand(time(NULL));
  } else {
    srand(rngSeed);
  }

  DoubleValue simV; GlobalValue::GetValueByName("simTime", simV);
  double simTime = simV.Get();
  StringValue outV; GlobalValue::GetValueByName("outDir", outV);
  fs::path outDir = outV.Get();

  // Read E2/Control GlobalValues
  BooleanValue booleanValue;
  StringValue stringValue;
  DoubleValue doubleValue;

  GlobalValue::GetValueByName ("useSemaphores", booleanValue);
  bool useSemaphores = booleanValue.Get ();
  GlobalValue::GetValueByName ("controlFileName", stringValue);
  std::string controlFilename = stringValue.Get ();
  GlobalValue::GetValueByName ("e2lteEnabled", booleanValue);
  bool e2lteEnabled = booleanValue.Get ();
  GlobalValue::GetValueByName ("e2nrEnabled", booleanValue);
  bool e2nrEnabled = booleanValue.Get ();
  GlobalValue::GetValueByName ("e2du", booleanValue);
  bool e2du = booleanValue.Get ();
  GlobalValue::GetValueByName ("e2cuUp", booleanValue);
  bool e2cuUp = booleanValue.Get ();
  GlobalValue::GetValueByName ("e2cuCp", booleanValue);
  bool e2cuCp = booleanValue.Get ();
  GlobalValue::GetValueByName ("reducedPmValues", booleanValue);
  bool reducedPmValues = booleanValue.Get ();
  GlobalValue::GetValueByName ("indicationPeriodicity", doubleValue);
  double indicationPeriodicity = doubleValue.Get ();
  GlobalValue::GetValueByName ("e2TermIp", stringValue);
  std::string e2TermIp = stringValue.Get ();
  GlobalValue::GetValueByName ("enableE2FileLogging", booleanValue);
  bool enableE2FileLogging = booleanValue.Get ();

  NS_LOG_UNCOND ("e2lteEnabled " << e2lteEnabled << " e2nrEnabled " << e2nrEnabled << " e2du "
                                 << e2du << " e2cuCp " << e2cuCp << " e2cuUp " << e2cuUp
                                 << " controlFilename " << controlFilename
                                 << " useSemaphores " << useSemaphores
                                 << " indicationPeriodicity " << indicationPeriodicity
                                 << " reducedPmValues " << reducedPmValues
                                 << " e2TermIp " << e2TermIp);

  //-----------------------E2 CONFIGURATION----------------------------
  // E2 periodicity on the device
  Config::SetDefault("ns3::MmWaveEnbNetDevice::E2Periodicity",
                     DoubleValue(indicationPeriodicity));

  // Helper-level E2 config
  Config::SetDefault("ns3::MmWaveHelper::E2ModeLte",
                     BooleanValue(e2lteEnabled));
  Config::SetDefault("ns3::MmWaveHelper::E2ModeNr",
                     BooleanValue(e2nrEnabled));
  Config::SetDefault("ns3::MmWaveHelper::E2Periodicity",
                     DoubleValue(indicationPeriodicity));
  Config::SetDefault("ns3::MmWaveHelper::E2TermIp",
                     StringValue(e2TermIp));

  // Device-level E2 report switches
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableDuReport",
                     BooleanValue(e2du));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuUpReport",
                     BooleanValue(e2cuUp));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuCpReport",
                     BooleanValue(e2cuCp));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableE2FileLogging",
                     BooleanValue(enableE2FileLogging));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::ReducedPmValues",
                     BooleanValue(reducedPmValues));
  //-------------------------------------------------------------------

  // RF/system defaults
  Config::SetDefault("ns3::MmWavePhyMacCommon::CenterFreq", DoubleValue(28e9));
  Config::SetDefault("ns3::MmWavePhyMacCommon::Bandwidth",  DoubleValue(56e6));
  Config::SetDefault("ns3::MmWaveEnbPhy::TxPower",          DoubleValue(10.0));
  Config::SetDefault("ns3::MmWaveUePhy::NoiseFigure",       DoubleValue(7.0));

  fs::create_directories(outDir);
  fs::current_path(outDir);

  Ptr<MmWaveHelper> mmw = CreateObject<MmWaveHelper>();
  Ptr<MmWavePointToPointEpcHelper> epc = CreateObject<MmWavePointToPointEpcHelper>();
  mmw->SetEpcHelper(epc);

  mmw->SetPathlossModelType("ns3::ThreeGppUmiStreetCanyonPropagationLossModel");
  mmw->SetChannelConditionModelType("ns3::ThreeGppUmiStreetCanyonChannelConditionModel");

  Ptr<Node> pgw = epc->GetPgwNode();

  NodeContainer gnb; gnb.Create(1);
  NodeContainer ue;  ue.Create(2);
  NodeContainer rh;  rh.Create(1);

  InternetStackHelper ip; ip.Install(ue); ip.Install(rh);

  // Mobility
  const Vector gnbPos = Vector(25,25,10);
  {
    MobilityHelper m;
    auto enbPos = CreateObject<ListPositionAllocator>();
    enbPos->Add(gnbPos);
    m.SetPositionAllocator(enbPos);
    m.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    m.Install(gnb);
  }

  // UE Mobility (Same as ourv2.cc)
  MobilityHelper uem;
  uem.SetMobilityModel("ns3::WaypointMobilityModel");
  uem.Install(ue);
  Ptr<WaypointMobilityModel> ueMobility = ue.Get(0)->GetObject<WaypointMobilityModel>();
  
  // ... (Waypoint logic same as ourv2.cc, simplified here for brevity but functional) ...
  ueMobility->AddWaypoint(Waypoint(Seconds(0.0), Vector(30, 25, 1.5)));
  ueMobility->AddWaypoint(Waypoint(Seconds(10.0), Vector(70, 25, 1.5)));
  ueMobility->AddWaypoint(Waypoint(Seconds(20.0), Vector(95, 25, 1.5)));
  ueMobility->AddWaypoint(Waypoint(Seconds(30.0), Vector(95, 110, 1.5)));
  ueMobility->AddWaypoint(Waypoint(Seconds(40.0), Vector(50, 110, 1.5)));
  ueMobility->AddWaypoint(Waypoint(Seconds(50.0), Vector(30, 25, 1.5)));

  // UE2 Mobility (High Speed Vehicle)
  MobilityHelper uem2;
  auto uePos2 = CreateObject<ListPositionAllocator>();
  uePos2->Add(Vector(10,10,1.5)); 
  uem2.SetPositionAllocator(uePos2);
  uem2.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
  uem2.Install(ue.Get(1));
  ue.Get(1)->GetObject<ConstantVelocityMobilityModel>()->SetVelocity(Vector(15.0, 5.0, 0.0)); // ~54 km/h

  // Core + RH
  {
    Ptr<Node> sgw = NodeList::GetNode(1);
    NodeContainer stationaryCoreNodes; stationaryCoreNodes.Add(pgw); stationaryCoreNodes.Add(sgw); stationaryCoreNodes.Add(rh.Get(0));
    MobilityHelper coreMobility; coreMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    Ptr<ListPositionAllocator> corePositions = CreateObject<ListPositionAllocator>();
    corePositions->Add(Vector(20.0,25.0,0.0));
    corePositions->Add(Vector(20.0,30.0,0.0));
    corePositions->Add(Vector(20.0,20.0,0.0));
    coreMobility.SetPositionAllocator(corePositions);
    coreMobility.Install(stationaryCoreNodes);
  }

  BuildingsHelper::Install(gnb);
  BuildingsHelper::Install(ue);

  NetDeviceContainer gnbDevs = mmw->InstallEnbDevice(gnb);
  NetDeviceContainer ueDevs  = mmw->InstallUeDevice(ue);

  Ipv4InterfaceContainer ueIf = epc->AssignUeIpv4Address(ueDevs);
  Ipv4StaticRoutingHelper srt;
  for (uint32_t u=0; u<ue.GetN(); ++u) {
    Ptr<Ipv4StaticRouting> r = srt.GetStaticRouting(ue.Get(u)->GetObject<Ipv4>());
    r->SetDefaultRoute(epc->GetUeDefaultGatewayAddress(), 1);
  }

  mmw->AttachToClosestEnb(ueDevs, gnbDevs);

  PointToPointHelper p2p;
  p2p.SetDeviceAttribute("DataRate", DataRateValue(DataRate("10Gb/s")));
  p2p.SetChannelAttribute("Delay", TimeValue(MilliSeconds(1)));
  NetDeviceContainer d = p2p.Install(pgw, rh.Get(0));
  Ipv4AddressHelper a; a.SetBase("10.0.0.0","255.0.0.0");
  a.Assign(d);
  Ipv4StaticRoutingHelper srh;
  srh.GetStaticRouting(rh.Get(0)->GetObject<Ipv4>())
     ->AddNetworkRouteTo(Ipv4Address("7.0.0.0"), Ipv4Mask("255.0.0.0"), 1);

  const uint16_t cbrPort = 4000;
  // UE1 Sink
  PacketSinkHelper sink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), cbrPort));
  ApplicationContainer sinkApps = sink.Install(ue.Get(0));
  sinkApps.Start(Seconds(0.2));
  Ptr<PacketSink> sinkApp = DynamicCast<PacketSink>(sinkApps.Get(0));

  // UE2 Sink (Background)
  PacketSinkHelper sink2("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), cbrPort+1));
  ApplicationContainer sinkApps2 = sink2.Install(ue.Get(1));
  sinkApps2.Start(Seconds(0.2));

  // UE1 Traffic Source (Bursty)
  OnOffHelper cbr("ns3::UdpSocketFactory", InetSocketAddress(ueIf.GetAddress(0), cbrPort));
  // Replace Constant with Exponential for burstiness
  cbr.SetAttribute("OnTime",  StringValue("ns3::ExponentialRandomVariable[Mean=1.0]"));
  cbr.SetAttribute("OffTime", StringValue("ns3::ExponentialRandomVariable[Mean=0.5]"));
  cbr.SetAttribute("DataRate", StringValue("50Mbps"));
  cbr.SetAttribute("PacketSize", UintegerValue(1200));
  ApplicationContainer cbrApps = cbr.Install(rh.Get(0));
  cbrApps.Start(Seconds(0.35));

  // Schedule dynamic traffic changes for UE1
  Simulator::Schedule(Seconds(10.0), &ChangeTrafficProfile, 
                     DynamicCast<OnOffApplication>(cbrApps.Get(0)), true);

  // UE2 Traffic Source (Constant Interference)
  OnOffHelper cbr2("ns3::UdpSocketFactory", InetSocketAddress(ueIf.GetAddress(1), cbrPort+1));
  cbr2.SetAttribute("OnTime",  StringValue("ns3::ConstantRandomVariable[Constant=1]"));
  cbr2.SetAttribute("OffTime", StringValue("ns3::ConstantRandomVariable[Constant=0]"));
  cbr2.SetAttribute("DataRate", StringValue("20Mbps")); // Moderate background load
  cbr2.SetAttribute("PacketSize", UintegerValue(1200));
  cbr2.Install(rh.Get(0)).Start(Seconds(1.0));

  mmw->EnableTraces();

  const double covRadius = 100.0;
  Simulator::Schedule(Seconds(0.1), &SampleAll,
                      std::ref(ue), std::ref(ueDevs), gnb.Get(0),
                      covRadius, sinkApp, 0.1);

  // NetAnim
  AnimationInterface anim("NetAnimFile_v3.xml");
  g_anim = &anim;
  anim.SetMobilityPollInterval(Seconds(1));
  anim.SkipPacketTracing();

  // Schedule MCS Events (Existing)
  Simulator::Schedule(Seconds(5.0), &ScheduleNextMcsEvent, gnb.Get(0), true);

  // Schedule NEW Random Events
  Simulator::Schedule(Seconds(12.0), &RandomBlockageEvent, ue, gnb.Get(0));
  Simulator::Schedule(Seconds(18.0), &TrafficSpikeEvent, rh);

  Simulator::Stop(Seconds(simTime));
  Simulator::Run();
  Simulator::Destroy();
  return 0;
}
