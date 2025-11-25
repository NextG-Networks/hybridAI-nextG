/*
==================================================================================

        Copyright (c) 2019-2020 AT&T Intellectual Property.

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
==================================================================================
*/
/*
 * msgs_proc.cc
 * Created on: 2019
 * Author: Ashwin Shridharan, Shraboni Jana
 */


 #include "msgs_proc.hpp"
 #include <stdio.h>
 #include <thread>
 #include <string>
 #include <cctype>
 // #include "xapp.hpp"
 
 #include "ai_tcp_client.h"
 
 // E2SM (HelloWorld) indication decode support available in this repo
 #include "../xapp-asn/e2sm/e2sm_indication.hpp"
 // E2SM-KPM ASN.1 types (from asn1c_defs)
 extern "C" {
 #include "asn_application.h"
 #include "E2SM-KPM-IndicationHeader.h"
 #include "E2SM-KPM-IndicationMessage.h"
 #include "E2SM-KPM-IndicationMessage-Format1.h"
 #include "PM-Info-Item.h"
 #include "MeasurementType.h"
 #include "MeasurementValue.h"
 #include "PerUE-PM-Item.h"
 #include "UE-Identity.h"
 #include "L3-RRC-Measurements.h"
 #include "MeasQuantityResults.h"
 #include "ServingCellMeasurements.h"
 #include "MeasResultNeighCells.h"
 #include "MeasResultServMOList.h"
 #include "MeasResultServMO.h"
 #include "MeasResultNR.h"
 #include "MeasResultListNR.h"
 #include "MeasResultPCell.h"
 #include "RRCEvent.h"
 }
 
 bool XappMsgHandler::encode_subscription_delete_request(unsigned char* buffer, size_t *buf_len){
 
	 subscription_helper sub_helper;
	 sub_helper.set_request(0); // requirement of subscription manager ... ?
	 sub_helper.set_function_id(0);
 
	 subscription_delete e2ap_sub_req_del;
 
	   // generate the delete request pdu
 
	   bool res = e2ap_sub_req_del.encode_e2ap_subscription(&buffer[0], buf_len, sub_helper);
	   if(! res){
		 mdclog_write(MDCLOG_ERR, "%s, %d: Error encoding subscription delete request pdu. Reason = %s", __FILE__, __LINE__, e2ap_sub_req_del.get_error().c_str());
		 return false;
	   }
 
	 return true;
 
 }
 
 bool XappMsgHandler::decode_subscription_response(unsigned char* data_buf, size_t data_size){
 
	 subscription_helper subhelper;
	 subscription_response subresponse;
	 bool res = true;
	 E2AP_PDU_t *e2pdu = 0;
 
	 asn_dec_rval_t rval;
 
	 ASN_STRUCT_RESET(asn_DEF_E2AP_PDU, e2pdu);
 
	 rval = asn_decode(0,ATS_ALIGNED_BASIC_PER, &asn_DEF_E2AP_PDU, (void**)&e2pdu, data_buf, data_size);
	 switch(rval.code)
	 {
		 case RC_OK:
				//Put in Subscription Response Object.
				//asn_fprint(stdout, &asn_DEF_E2AP_PDU, e2pdu);
				break;
		 case RC_WMORE:
				 mdclog_write(MDCLOG_ERR, "RC_WMORE");
				 res = false;
				 break;
		 case RC_FAIL:
				 mdclog_write(MDCLOG_ERR, "RC_FAIL");
				 res = false;
				 break;
		 default:
				 break;
	  }
	 ASN_STRUCT_FREE(asn_DEF_E2AP_PDU, e2pdu);
	 return res;
 
 }
 
 bool  XappMsgHandler::a1_policy_handler(char * message, int *message_len, a1_policy_helper &helper){
 
   rapidjson::Document doc;
   if (doc.Parse(message).HasParseError()){
	 mdclog_write(MDCLOG_ERR, "Error: %s, %d :: Could not decode A1 JSON message %s\n", __FILE__, __LINE__, message);
	 return false;
   }
 
   //Extract Operation
   rapidjson::Pointer temp1("/operation");
	 rapidjson::Value * ref1 = temp1.Get(doc);
	 if (ref1 == NULL){
	   mdclog_write(MDCLOG_ERR, "Error : %s, %d:: Could not extract policy type id from %s\n", __FILE__, __LINE__, message);
	   return false;
	 }
 
	helper.operation = ref1->GetString();
 
   // Extract policy id type
   rapidjson::Pointer temp2("/policy_type_id");
   rapidjson::Value * ref2 = temp2.Get(doc);
   if (ref2 == NULL){
	 mdclog_write(MDCLOG_ERR, "Error : %s, %d:: Could not extract policy type id from %s\n", __FILE__, __LINE__, message);
	 return false;
   }
	//helper.policy_type_id = ref2->GetString();
	 helper.policy_type_id = to_string(ref2->GetInt());
 
	 // Extract policy instance id
	 rapidjson::Pointer temp("/policy_instance_id");
	 rapidjson::Value * ref = temp.Get(doc);
	 if (ref == NULL){
	   mdclog_write(MDCLOG_ERR, "Error : %s, %d:: Could not extract policy type id from %s\n", __FILE__, __LINE__, message);
	   return false;
	 }
	 helper.policy_instance_id = ref->GetString();
 
	 if (helper.policy_type_id == "1" && helper.operation == "CREATE"){
		 helper.status = "OK";
		 Document::AllocatorType& alloc = doc.GetAllocator();
 
		 Value handler_id;
		 handler_id.SetString(helper.handler_id.c_str(), helper.handler_id.length(), alloc);
 
		 Value status;
		 status.SetString(helper.status.c_str(), helper.status.length(), alloc);
 
 
		 doc.AddMember("handler_id", handler_id, alloc);
		 doc.AddMember("status",status, alloc);
		 doc.RemoveMember("operation");
		 StringBuffer buffer;
		 Writer<StringBuffer> writer(buffer);
		 doc.Accept(writer);
		 strncpy(message,buffer.GetString(), buffer.GetLength());
		 *message_len = buffer.GetLength();
		 return true;
	 }
	 return false;
  }
 
 
 static inline void PublishKpiToExternal(const std::string& meid,
										 const std::string& kpi_json)
 {
	 GetAiTcpClient().SendKpi(meid, kpi_json);
 }
 
 static inline std::string RequestRecommendation(const std::string& meid,
												 const std::string& kpi_json)
 {
	 // // For testing: send bandwidth change after first indication
	 // static std::atomic<bool> first_indication_sent{false};
	 // static std::mutex test_mutex;
	 
	 // {
	 //     std::lock_guard<std::mutex> lock(test_mutex);
	 //     if (!first_indication_sent) {
	 //         first_indication_sent = true;
			 
	 //         // Schedule test command after 10 seconds
	 //         std::thread([meid]() {
	 //             std::this_thread::sleep_for(std::chrono::seconds(10));
				 
	 //             // Send bandwidth change command
	 //             std::string cmd = "{\"cmd\":\"set-bandwidth\",\"node\":0,\"bandwidth\":0}";
				 
	 //             // Get access to xApp instance to send command
	 //             // Note: You'll need to pass the control sender callback here
	 //             // For now, this is a placeholder - see Option 2 for better approach
	 //             mdclog_write(MDCLOG_INFO, "[TEST] Would send command: %s to %s", 
	 //                         cmd.c_str(), meid.c_str());
	 //         }).detach();
	 //     }
	 // }
	 // // return "{\"cmd\":\"set-bandwidth\",\"node\":0,\"bandwidth\":0}";
	 // return "{\"cmd\":\"set-mcs\",\"node\":2,\"mcs\":0}";
 
 
	 
	 // Original AI recommendation logic
	 std::string cmd;
	 mdclog_write(MDCLOG_INFO, "Requesting recommendation from AI for MEID=%s", meid.c_str());
	 if (GetAiTcpClient().GetRecommendation(meid, kpi_json, cmd)) {
		 mdclog_write(MDCLOG_INFO, "Sending control command to ns-3: %s", cmd.c_str());
		 return cmd;
	 }
	 return "";
 }
 
 
 
 //For processing received messages.XappMsgHandler should mention if resend is required or not.
 void XappMsgHandler::operator()(rmr_mbuf_t *message, bool *resend){
 
	 if (message->len > MAX_RMR_RECV_SIZE){
		 mdclog_write(MDCLOG_ERR, "Error : %s, %d, RMR message larger than %d. Ignoring ...", __FILE__, __LINE__, MAX_RMR_RECV_SIZE);
		 return;
	 }
	 a1_policy_helper helper;
	 bool res=false;
	 switch(message->mtype){
		 //need to fix the health check.
		 case (RIC_HEALTH_CHECK_REQ):
				 message->mtype = RIC_HEALTH_CHECK_RESP;        // if we're here we are running and all is ok
				 message->sub_id = -1;
				 strncpy( (char*)message->payload, "HELLOWORLD OK\n", rmr_payload_size( message) );
				 *resend = true;
				 break;
 
		 case RIC_INDICATION: {
			 mdclog_write(MDCLOG_INFO, "Received RIC indication message of type = %d", message->mtype);
 
			 unsigned char* me_id_null = nullptr;
			 unsigned char* me_id = rmr_get_meid(message, me_id_null);
			 if (me_id == nullptr) {
				 mdclog_write(MDCLOG_ERR, "RIC_INDICATION missing MEID; ignoring");
				 break;
			 }
 
			 std::string meid_str(reinterpret_cast<char*>(me_id));
 
			 // Decode E2SM and get decoded JSON
			 std::string decoded_json = process_ric_indication(message->mtype, me_id, message->payload, message->len, me_id);
 
			 // 1) Forward decoded KPI to external system (non-blocking, fire-and-forget)
			 // Only send if we successfully decoded, otherwise skip (don't send raw hex)
			 if (!decoded_json.empty()) {
				 PublishKpiToExternal(meid_str, decoded_json);
				 // Note: The external system will reactively send control commands back
				 // via the control command listener (set up in main). No polling needed.
			 } else {
				 mdclog_write(MDCLOG_WARN, "Failed to decode E2SM message for MEID=%s, skipping", meid_str.c_str());
			 }
 
			 // 2) REMOVED: No longer polling for recommendations.
			 // The external system will reactively send control commands when ready.
			 // Control commands are handled by the listener set up in main().
			 break;
		 }
 
		 case (RIC_SUB_RESP): {
				 mdclog_write(MDCLOG_INFO, "Received subscription message of type = %d", message->mtype);
 
				 unsigned char *me_id_null = nullptr;
				 unsigned char *me_id = rmr_get_meid(message, me_id_null);
				 mdclog_write(MDCLOG_INFO,"RMR Received MEID: %s",me_id);
 
				 if(_ref_sub_handler !=NULL){
					 _ref_sub_handler->manage_subscription_response(message->mtype, me_id, message->payload, message->len);
				 } else {
					 mdclog_write(MDCLOG_ERR, " Error :: %s, %d : Subscription handler not assigned in message processor !", __FILE__, __LINE__);
				 }
				 *resend = false;
				 break;
		  }
 
	 case A1_POLICY_REQ:
 
			 mdclog_write(MDCLOG_INFO, "In Message Handler: Received A1_POLICY_REQ.");
			 helper.handler_id = xapp_id;
 
			 res = a1_policy_handler((char*)message->payload, &message->len, helper);
			 if(res){
				 message->mtype = A1_POLICY_RESP;        // if we're here we are running and all is ok
				 message->sub_id = -1;
				 *resend = true;
			 }
			 break;
 
	 default:
		 {
			 mdclog_write(MDCLOG_ERR, "Error :: Unknown message type %d received from RMR", message->mtype);
			 *resend = false;
		 }
	 }
 
	 return;
 
 };
 
 std::string process_ric_indication(int message_type, transaction_identifier id, const void *message_payload, size_t message_len, const unsigned char* me_id) {
 
	 std::cout << "In Process RIC indication" << std::endl;
	 std::cout << "ID " << id << std::endl;
 
	 // decode received message payload
   E2AP_PDU_t *pdu = nullptr;
   auto retval = asn_decode(nullptr, ATS_ALIGNED_BASIC_PER, &asn_DEF_E2AP_PDU, (void **) &pdu, message_payload, message_len);
 
   // Extract MEID string if available
   std::string meid_str;
   if (me_id != nullptr) {
       meid_str = std::string(reinterpret_cast<const char*>(me_id));
   }
 
   // print decoded payload
   if (retval.code == RC_OK) {
	 char *printBuffer;
	 size_t size;
	 FILE *stream = open_memstream(&printBuffer, &size);
	 asn_fprint(stream, &asn_DEF_E2AP_PDU, pdu);
	 mdclog_write(MDCLOG_DEBUG, "Decoded E2AP PDU: %s", printBuffer);
 
	 return procRicIndication(pdu, id, meid_str);
   }
	 else {
		 std::cout << "process_ric_indication, retval.code " << retval.code << std::endl;
		 return std::string();
	 }
 }
 
 /**
  * Handle RIC indication
  * Returns decoded JSON string if successful, empty string otherwise
  */
 std::string procRicIndication(E2AP_PDU_t *e2apMsg, transaction_identifier gnb_id, const std::string& meid_str)
 {
	uint8_t idx;
	RICindication_t *ricIndication;
 
	printf("\nE2AP : RIC Indication received");
	ricIndication = &e2apMsg->choice.initiatingMessage->value.choice.RICindication;
 
	printf("protocolIEs elements %d\n", ricIndication->protocolIEs.list.count);
 
	for (idx = 0; idx < ricIndication->protocolIEs.list.count; idx++)
	{
	   switch(ricIndication->protocolIEs.list.array[idx]->id)
	   {
				 case 28:  // RIC indication type
				 {
					 long ricindicationType = ricIndication->protocolIEs.list.array[idx]-> \
																		  value.choice.RICindicationType;
 
					 printf("ricindicationType %ld\n", ricindicationType);
 
					 break;
				 }
				 case 26:  // RIC indication message
				 {
					 size_t payload_size = ricIndication->protocolIEs.list.array[idx]-> \
																		  value.choice.RICindicationMessage.size;
 
					 char* payload = (char*) calloc(payload_size, sizeof(char));
					 memcpy(payload, ricIndication->protocolIEs.list.array[idx]-> \
																		  value.choice.RICindicationMessage.buf, payload_size);
 
					 // Helper lambda
					 auto json_escape = [](const unsigned char* data, size_t len) {
						 std::string s;
						 s.reserve(len + 8);
						 for (size_t i = 0; i < len; ++i) {
							 unsigned char c = data[i];
							 switch (c) {
								 case '\"': s += "\\\""; break;
								 case '\\': s += "\\\\"; break;
								 case '\b': s += "\\b"; break;
								 case '\f': s += "\\f"; break;
								 case '\n': s += "\\n"; break;
								 case '\r': s += "\\r"; break;
								 case '\t': s += "\\t"; break;
								 default:
									 if (c < 0x20) {
										 char buf[7];
										 snprintf(buf, sizeof(buf), "\\u%04x", c);
										 s += buf;
									 } else {
										 s.push_back((char)c);
									 }
							 }
						 }
						 return s;
					 };
 
					 // Helper lambda to extract RSRP/RSRQ/SINR from MeasQuantityResults
					 auto extract_signal_quality = [](MeasQuantityResults_t* mq) -> std::string {
						 if (!mq) return "";
						 std::string sq = "{";
						 bool has_any = false;
						 
						 if (mq->rsrp) {
							 sq += "\"rsrp\":" + std::to_string(*mq->rsrp);
							 has_any = true;
						 }
						 if (mq->rsrq) {
							 if (has_any) sq += ",";
							 sq += "\"rsrq\":" + std::to_string(*mq->rsrq);
							 has_any = true;
						 }
						 if (mq->sinr) {
							 if (has_any) sq += ",";
							 sq += "\"sinr\":" + std::to_string(*mq->sinr);
							 has_any = true;
						 }
						 
						 sq += "}";
						 return has_any ? sq : "";
					 };
 
					 // Helper lambda to extract measurement from PM_Info_Item
					 auto extract_measurement = [&json_escape, &extract_signal_quality](PM_Info_Item_t* pm_item) -> std::string {
						 if (!pm_item) return "";
						 std::string meas = "{";
						 
						 // Extract measurement type (name or ID)
						 if (pm_item->pmType.present == MeasurementType_PR_measName) {
							 if (pm_item->pmType.choice.measName.buf && pm_item->pmType.choice.measName.size > 0) {
								 std::string meas_name((char*)pm_item->pmType.choice.measName.buf, 
													  pm_item->pmType.choice.measName.size);
								 meas += "\"name\":\"" + json_escape((unsigned char*)meas_name.c_str(), meas_name.size()) + "\"";
							 }
						 } else if (pm_item->pmType.present == MeasurementType_PR_measID) {
							 meas += "\"id\":" + std::to_string(pm_item->pmType.choice.measID);
						 }
						 
						 // Extract measurement value
						 if (pm_item->pmVal.present == MeasurementValue_PR_valueInt) {
							 meas += ",\"value\":" + std::to_string(pm_item->pmVal.choice.valueInt);
						 } else if (pm_item->pmVal.present == MeasurementValue_PR_valueReal) {
							 char buf[64];
							 snprintf(buf, sizeof(buf), "%.6f", pm_item->pmVal.choice.valueReal);
							 meas += ",\"value\":" + std::string(buf);
						 } else if (pm_item->pmVal.present == MeasurementValue_PR_noValue) {
							 meas += ",\"value\":null";
						 } else if (pm_item->pmVal.present == MeasurementValue_PR_valueRRC) {
							 // Extract RRC measurements (RSRP, RSRQ, SINR) - critical for network optimization!
							 L3_RRC_Measurements_t* rrc = pm_item->pmVal.choice.valueRRC;
							 if (rrc) {
								 meas += ",\"rrcEvent\":";
								 if (rrc->rrcEvent == RRCEvent_b1) meas += "\"b1\"";
								 else if (rrc->rrcEvent == RRCEvent_a3) meas += "\"a3\"";
								 else if (rrc->rrcEvent == RRCEvent_a5) meas += "\"a5\"";
								 else if (rrc->rrcEvent == RRCEvent_periodic) meas += "\"periodic\"";
								 else {
									 char buf[32];
									 snprintf(buf, sizeof(buf), "\"%ld\"", rrc->rrcEvent);
									 meas += buf;
								 }
								 
								 // Extract serving cell measurements (RSRP/RSRQ/SINR)
								 if (rrc->servingCellMeasurements) {
									 if (rrc->servingCellMeasurements->present == ServingCellMeasurements_PR_nr_measResultServingMOList) {
										 MeasResultServMOList_t* serv_list = rrc->servingCellMeasurements->choice.nr_measResultServingMOList;
										 if (serv_list && serv_list->list.count > 0) {
											 meas += ",\"servingCells\":[";
											 for (size_t i = 0; i < serv_list->list.count; i++) {
												 MeasResultServMO_t* serv_mo = serv_list->list.array[i];
												 if (serv_mo) {
													 if (i > 0) meas += ",";
													 meas += "{\"servCellId\":" + std::to_string(serv_mo->servCellId);
													 if (serv_mo->measResultServingCell.measResult.cellResults.resultsSSB_Cell) {
														 std::string sq = extract_signal_quality(serv_mo->measResultServingCell.measResult.cellResults.resultsSSB_Cell);
														 if (!sq.empty()) meas += ",\"signalQuality\":" + sq;
													 }
													 meas += "}";
												 }
											 }
											 meas += "]";
										 }
									 } else if (rrc->servingCellMeasurements->present == ServingCellMeasurements_PR_eutra_measResultPCell) {
										 MeasResultPCell_t* pcell = rrc->servingCellMeasurements->choice.eutra_measResultPCell;
										 if (pcell) {
											 meas += ",\"servingCell\":{";
											 meas += "\"physCellId\":" + std::to_string(pcell->eutra_PhysCellId);
											 meas += ",\"rsrp\":" + std::to_string(pcell->rsrpResult);
											 meas += ",\"rsrq\":" + std::to_string(pcell->rsrqResult);
											 meas += "}";
										 }
									 }
								 }
								 
								 // Extract neighbor cell measurements (for handover optimization)
								 if (rrc->measResultNeighCells) {
									 if (rrc->measResultNeighCells->present == MeasResultNeighCells_PR_measResultListNR) {
										 MeasResultListNR_t* neigh_list = rrc->measResultNeighCells->choice.measResultListNR;
										 if (neigh_list && neigh_list->list.count > 0) {
											 meas += ",\"neighborCells\":[";
											 for (size_t i = 0; i < neigh_list->list.count && i < 8; i++) { // maxCellReport = 8
												 MeasResultNR_t* neigh = neigh_list->list.array[i];
												 if (neigh) {
													 if (i > 0) meas += ",";
													 meas += "{";
													 if (neigh->physCellId) meas += "\"physCellId\":" + std::to_string(*neigh->physCellId);
													 if (neigh->measResult.cellResults.resultsSSB_Cell) {
														 std::string sq = extract_signal_quality(neigh->measResult.cellResults.resultsSSB_Cell);
														 if (!sq.empty()) {
															 if (neigh->physCellId) meas += ",";
															 meas += "\"signalQuality\":" + sq;
														 }
													 }
													 meas += "}";
												 }
											 }
											 meas += "]";
										 }
									 }
								 }
							 }
						 }
						 
						 meas += "}";
						 return meas;
					 };
 
					 std::string out_json;
					 bool decoded_ok = false;
 
					 // 1) Try E2SM-KPM
					 E2SM_KPM_IndicationMessage_t *kpm = 0;
					 asn_dec_rval_t kpm_res = asn_decode(0,
														 ATS_ALIGNED_BASIC_PER,
														 &asn_DEF_E2SM_KPM_IndicationMessage,
														 (void**)&kpm,
														 payload,
														 payload_size);
					 if (kpm && kpm_res.code == RC_OK) {
						 // Decode KPM Format1 and extract measurement data
						 if (kpm->present == E2SM_KPM_IndicationMessage_PR_indicationMessage_Format1) {
							 E2SM_KPM_IndicationMessage_Format1_t* f1 = kpm->choice.indicationMessage_Format1;
							 if (f1) {
								 // Helper function to extract node_id from MEID or cell_id
								 auto extract_node_id = [&meid_str](const std::string& cell_id) -> int {
									 // First, try to extract from MEID (most reliable)
									 if (!meid_str.empty()) {
										 // MEID format: "gnb:131-133-31000000" -> node 2 (gNB)
										 // MEID format: "ue:..." -> node 3 (UE)
										 if (meid_str.find("gnb:") == 0) {
											 return 2; // gNB is node 2
										 } else if (meid_str.find("ue:") == 0 || meid_str.find("UE:") == 0) {
											 return 3; // UE is node 3
										 }
									 }
									 
									 // Fallback: try to extract from cell_id
									 if (cell_id.empty() || cell_id == "NRCellCU" || cell_id == "unknown" || cell_id == "N/A") {
										 return -1; // Invalid
									 }
									 
									 // Extract numeric part from cell_id
									 std::string numeric_part;
									 if (cell_id.find("CELL_") == 0) {
										 // Format: "CELL_2" or "CELL_1111" -> extract numeric part
										 numeric_part = cell_id.substr(5);
									 } else if (cell_id.find_first_of("0123456789") != std::string::npos) {
										 // Format: "1111" or "2" -> extract only digits
										 for (char c : cell_id) {
											 if (std::isdigit(c)) {
												 numeric_part += c;
											 }
										 }
									 }
									 
									 // For cell_id like "1111", if MEID indicates gNB, return node 2
									 // Otherwise, try to extract meaningful node ID
									 if (!numeric_part.empty()) {
										 // If cell_id is "1111" and we have a gNB MEID, it's node 2
										 if (numeric_part == "1111" && !meid_str.empty() && meid_str.find("gnb:") == 0) {
											 return 2;
										 }
										 // Try to parse as integer (for formats like "CELL_2")
										 try {
											 int parsed = std::stoi(numeric_part);
											 // If it's a single digit or small number, use it directly
											 if (parsed >= 0 && parsed <= 9) {
												 return parsed;
											 }
										 } catch (...) {
											 // Not a valid integer, continue
										 }
									 }
									 
									 return -1; // Could not extract
								 };
								 
								 // Start building JSON with basic info
								 std::string json = "{\"serviceModel\":\"KPM\",\"format\":\"F1\"";
								 
								 // Add cellObjectID and node_id if present
								 int node_id = -1;
								 std::string cell_id;
								 if (f1->cellObjectID.buf && f1->cellObjectID.size > 0) {
									 cell_id = std::string((char*)f1->cellObjectID.buf, f1->cellObjectID.size);
									 // Only add if it's a valid cell ID (not "NRCellCU" or empty)
									 if (cell_id != "NRCellCU" && !cell_id.empty()) {
										 json += ",\"cellObjectID\":\"" + json_escape((unsigned char*)cell_id.c_str(), cell_id.size()) + "\"";
									 }
								 }
								 
								 // Extract node_id from MEID (preferred) or cell_id (fallback)
								 node_id = extract_node_id(cell_id);
								 if (node_id >= 0) {
									 json += ",\"node_id\":" + std::to_string(node_id);
								 }
								 
								 // Extract PM measurements from list_of_PM_Information (cell-level)
								 if (f1->list_of_PM_Information && f1->list_of_PM_Information->list.count > 0) {
									 json += ",\"measurements\":[";
									 for (size_t i = 0; i < f1->list_of_PM_Information->list.count; i++) {
										 PM_Info_Item_t* pm_item = f1->list_of_PM_Information->list.array[i];
										 if (pm_item) {
											 if (i > 0) json += ",";
											 json += extract_measurement(pm_item);
										 }
									 }
									 json += "]";
								 }
								 
								 // Extract per-UE measurements from list_of_matched_UEs
								 if (f1->list_of_matched_UEs && f1->list_of_matched_UEs->list.count > 0) {
									 json += ",\"ues\":[";
									 for (size_t i = 0; i < f1->list_of_matched_UEs->list.count; i++) {
										 PerUE_PM_Item_t* ue_item = f1->list_of_matched_UEs->list.array[i];
										 if (ue_item) {
											 if (i > 0) json += ",";
											 json += "{";
											 
											 // Add node_id = 3 for UE measurements
											 json += "\"node_id\":3";
											 
											 // Extract UE ID
											 if (ue_item->ueId.buf && ue_item->ueId.size > 0) {
												 std::string ue_id_hex;
												 ue_id_hex.reserve(ue_item->ueId.size * 2);
												 static const char* hex_chars = "0123456789abcdef";
												 for (size_t j = 0; j < ue_item->ueId.size; j++) {
													 unsigned char c = ue_item->ueId.buf[j];
													 ue_id_hex += hex_chars[(c >> 4) & 0xF];
													 ue_id_hex += hex_chars[c & 0xF];
												 }
												 json += ",\"ueId\":\"" + ue_id_hex + "\"";
											 }
											 
											 // Extract per-UE measurements
											 if (ue_item->list_of_PM_Information && ue_item->list_of_PM_Information->list.count > 0) {
												 json += ",\"measurements\":[";
												 for (size_t j = 0; j < ue_item->list_of_PM_Information->list.count; j++) {
													 PM_Info_Item_t* pm_item = ue_item->list_of_PM_Information->list.array[j];
													 if (pm_item) {
														 if (j > 0) json += ",";
														 json += extract_measurement(pm_item);
													 }
												 }
												 json += "]";
											 }
											 
											 json += "}";
										 }
									 }
									 json += "]";
								 }
								 
								 // Add PM containers count
								 size_t pm_cont_count = f1->pm_Containers.list.count;
								 json += ",\"pmContainers\":" + std::to_string(pm_cont_count);
								 
								 json += "}";
								 out_json = json;
								 decoded_ok = true;
								 size_t meas_count = f1->list_of_PM_Information ? f1->list_of_PM_Information->list.count : 0;
								 size_t ue_count = f1->list_of_matched_UEs ? f1->list_of_matched_UEs->list.count : 0;
								 mdclog_write(MDCLOG_INFO, "Decoded KPM E2SM message Format1 (pmContainers=%zu, measurements=%zu, ues=%zu)", 
											  pm_cont_count, meas_count, ue_count);
							 }
						 } else {
							 out_json = "{\"serviceModel\":\"KPM\",\"format\":\"unknown\"}";
							 decoded_ok = true;
							 mdclog_write(MDCLOG_INFO, "Decoded KPM E2SM message (present=%d, unsupported format)", kpm->present);
						 }
					 }
 
					 // 2) If KPM not decoded, try HelloWorld
					 if (!decoded_ok) {
						 E2SM_HelloWorld_IndicationMessage_t *hw_msg = 0;
						 asn_dec_rval_t dres = asn_decode(0,
														  ATS_ALIGNED_BASIC_PER,
														  &asn_DEF_E2SM_HelloWorld_IndicationMessage,
														  (void**)&hw_msg,
														  payload,
														  payload_size);
						 if (dres.code == RC_OK && hw_msg != nullptr) {
							 e2sm_indication helper_iface;
							 e2sm_indication_helper decoded;
							 if (helper_iface.get_fields(hw_msg, decoded)) {
								 std::string msg_str = json_escape(decoded.message, decoded.message_len);
								 out_json = std::string("{\"serviceModel\":\"HelloWorld\",\"indicationMessage\":\"") +
											msg_str + "\"}";
								 decoded_ok = true;
								 mdclog_write(MDCLOG_INFO, "Decoded HelloWorld E2SM message, len=%zu", decoded.message_len);
							 } else {
								 mdclog_write(MDCLOG_WARN, "HelloWorld decode get_fields failed");
							 }
						 } else {
							 mdclog_write(MDCLOG_WARN, "E2SM HelloWorld decode failed (code=%d)", dres.code);
						 }
						 if (hw_msg) {
							 ASN_STRUCT_FREE(asn_DEF_E2SM_HelloWorld_IndicationMessage, hw_msg);
						 }
					 }
 
					 if (kpm) {
						 ASN_STRUCT_FREE(asn_DEF_E2SM_KPM_IndicationMessage, kpm);
					 }
					 free(payload);
 
					 // Return decoded JSON if available, empty string otherwise
					 return decoded_ok ? out_json : std::string();
				 }
	   }
	}
	return std::string(); // No E2SM payload found or decoded
 }
 